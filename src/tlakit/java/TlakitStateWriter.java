// Stream TLC's state graph as NDJSON, one record per state and per edge.
//
// TLC's `-dump dot` writes the whole reachable graph to disk as Graphviz and
// only after the run has finished, so nothing is visible while a check is
// working and a killed run leaves nothing behind. `-dump dot` is not special
// to the checker, though: it is an `IStateWriter`, and the writer is settable
// through `TLC.setStateWriter`. This is that seam, taken.
//
// Records are written as they are generated and flushed one at a time, so a
// reader sees the graph grow and a run cut short by a budget still leaves
// every state it reached:
//
//     {"t":"state","id":"-2952126410107482618","initial":true,"vars":{"x":"0"}}
//     {"t":"edge","from":"-295...","to":"129...","flag":"unseen","action":"Next"}
//
// Ids are TLC's own state fingerprints, and are signed 64-bit -- often
// negative -- so they are written as strings. They are also only comparable
// within one run: TLC picks its fingerprint polynomial per run unless `-fp`
// pins it.
//
// Variable values are `IValue.toString()`, which is TLA+ source form -- the
// same text `-dump dot` puts in a node label. Rendering them into JSON
// structure instead would be a second, worse `-dumpTrace json`.
//
// `main` mirrors `tlc2.TLC.main`, minus the MailSender (tlakit never passes
// `-mailto`) and the private GC warning: parameters, then the resolver the
// main file's directory implies, then the writer, then `process()`, and the
// same `errorConstantToExitStatus` mapping -- `process()` returns an error
// constant, not an exit status, and callers read the exit status.
//
// Compiled on demand by `tlakit.statewriter` against the same tla2tools.jar
// the run uses, so this file has no build step of its own.

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Map;

import tla2sany.semantic.SemanticNode;
import tlc2.TLC;
import tlc2.output.EC;
import tlc2.tool.Action;
import tlc2.tool.TLCState;
import tlc2.util.BitVector;
import tlc2.util.IStateWriter;
import tlc2.value.IValue;
import util.FileUtil;
import util.SimpleFilenameToStream;
import util.UniqueString;

public final class TlakitStateWriter implements IStateWriter {

    /** The system property naming the file to stream NDJSON into. */
    private static final String OUT_PROPERTY = "tlakit.graph.out";

    private final Writer out;
    private final String name;

    private TlakitStateWriter(final String path) throws IOException {
        this.name = path;
        this.out = new BufferedWriter(
                Files.newBufferedWriter(Paths.get(path), StandardCharsets.UTF_8));
    }

    public static void main(final String[] args) throws Exception {
        final String dump = System.getProperty(OUT_PROPERTY);
        if (dump == null || dump.isEmpty()) {
            System.err.println("tlakit: -D" + OUT_PROPERTY + "=<path> is required");
            System.exit(1);
        }
        final TLC tlc = new TLC();
        if (!tlc.handleParameters(args)) {
            System.exit(1);
        }
        // What TLC.main does. tlakit itself always runs TLC with the working
        // directory *as* the spec's directory and passes a bare filename, so
        // TLC's default resolver already finds sibling modules and removing
        // this changes nothing there -- measured 2026-08-08. It stays because
        // this is a general TLC front end: a main file named with a directory
        // component would otherwise resolve its EXTENDS somewhere else than it
        // does under `tlc2.TLC`.
        final String directory = FileUtil.parseDirname(tlc.getMainFile());
        tlc.setResolver(directory.isEmpty() ? new SimpleFilenameToStream()
                                           : new SimpleFilenameToStream(directory));
        final TlakitStateWriter writer = new TlakitStateWriter(dump);
        // After handleParameters, so this wins over any -dump on the command
        // line rather than racing it.
        tlc.setStateWriter(writer);
        final int code;
        try {
            code = tlc.process();
        } finally {
            writer.close();
        }
        System.exit(EC.ExitStatus.errorConstantToExitStatus(code));
    }

    // --- the stream ---------------------------------------------------------

    private void emit(final String record) {
        try {
            // One write, so the buffer holds a whole record: a JVM killed
            // mid-run then leaves complete lines rather than half of one.
            out.write(record + "\n");
            out.flush();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private void emitState(final TLCState state, final boolean initial) {
        final StringBuilder b = new StringBuilder(128);
        b.append("{\"t\":\"state\",\"id\":\"").append(state.fingerPrint()).append('"');
        if (initial) {
            b.append(",\"initial\":true");
        }
        b.append(",\"vars\":{");
        boolean first = true;
        for (final Map.Entry<UniqueString, IValue> entry : state.getVals().entrySet()) {
            if (entry.getValue() == null) {
                // A variable left unassigned by a partially evaluated state.
                continue;
            }
            if (!first) {
                b.append(',');
            }
            first = false;
            quote(b, entry.getKey().toString());
            b.append(':');
            quote(b, entry.getValue().toString());
        }
        b.append("}}");
        emit(b.toString());
    }

    private void emitEdge(final TLCState from, final TLCState to, final short flag,
                          final Action action) {
        final StringBuilder b = new StringBuilder(96);
        b.append("{\"t\":\"edge\",\"from\":\"").append(from.fingerPrint())
         .append("\",\"to\":\"").append(to.fingerPrint())
         .append("\",\"flag\":\"").append(flagName(flag))
         .append("\",\"action\":");
        // getInvocationSignature is what DotStateWriter labels an edge with,
        // and it carries the arguments: `Read("a")`, not `Read`.
        quote(b, action == null ? "" : action.getInvocationSignature().trim());
        b.append('}');
        emit(b.toString());
    }

    /** Whether the successor was new, already known, or outside the model. */
    private static String flagName(final short flag) {
        if (flag == IStateWriter.IsUnseen) {
            return "unseen";
        }
        if (flag == IStateWriter.IsSeen) {
            return "seen";
        }
        if (flag == IStateWriter.IsNotInModel) {
            return "notinmodel";
        }
        return "unknown";
    }

    private static void quote(final StringBuilder b, final String text) {
        b.append('"');
        for (int i = 0; i < text.length(); i++) {
            final char c = text.charAt(i);
            switch (c) {
                case '"':
                    b.append("\\\"");
                    break;
                case '\\':
                    b.append("\\\\");
                    break;
                case '\n':
                    b.append("\\n");
                    break;
                case '\r':
                    b.append("\\r");
                    break;
                case '\t':
                    b.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        b.append('"');
    }

    // --- IStateWriter -------------------------------------------------------
    //
    // Every overload funnels into the five-argument one, which is what
    // DotStateWriter does too. The BitVector overloads carry per-action
    // constraint checks that a graph does not render.

    @Override
    public synchronized void writeState(final TLCState state) {
        // No predecessor: an initial state.
        emitState(state, true);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final short flag) {
        writeState(from, to, flag, null, null);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final short flag, final Action action) {
        writeState(from, to, flag, action, null);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final short flag, final Action action,
                                        final SemanticNode pred) {
        if (flag == IStateWriter.IsUnseen) {
            // Only new states carry their variables: a seen successor was
            // written when it was first reached, and a reader keys on the id.
            emitState(to, false);
        }
        emitEdge(from, to, flag, action);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final short flag, final Visualization v) {
        writeState(from, to, flag, null, null);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final BitVector actionChecks, final int from2,
                                        final int length, final short flag) {
        writeState(from, to, flag, null, null);
    }

    @Override
    public synchronized void writeState(final TLCState from, final TLCState to,
                                        final BitVector actionChecks, final int from2,
                                        final int length, final short flag,
                                        final Visualization v) {
        writeState(from, to, flag, null, null);
    }

    @Override
    public synchronized void close() {
        try {
            out.close();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @Override
    public String getDumpFileName() {
        return name;
    }

    @Override
    public boolean isNoop() {
        return false;
    }

    @Override
    public boolean isDot() {
        // Not Graphviz. TLC asks so it can keep DOT's rank bookkeeping.
        return false;
    }

    @Override
    public boolean isConstrained() {
        return false;
    }

    @Override
    public synchronized void snapshot() throws IOException {
        out.flush();
    }
}
