"""Issue #25: the kernel end to end, through a real Jupyter kernelspec."""
import shutil

import pytest

pytest.importorskip("nbclient")
pytest.importorskip("jupyter_client")

import nbformat  # noqa: E402
from nbclient import NotebookClient  # noqa: E402

import tlakit  # noqa: E402

pytestmark = pytest.mark.java

MICROWAVE = """---- MODULE Microwave ----
EXTENDS Naturals
VARIABLES door, radiation
vars == <<door, radiation>>
Init == door = "closed" /\\ radiation = "off"
Open  == door' = "open"   /\\ UNCHANGED radiation
Close == door' = "closed" /\\ UNCHANGED radiation
Start == radiation' = "on" /\\ UNCHANGED door
Next == Open \\/ Close \\/ Start
Spec == Init /\\ [][Next]_vars
Safety == radiation = "on" => door = "closed"
===="""


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    """Install the kernelspec into a throwaway Jupyter data dir."""
    import os

    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        import tlakit

        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    data_dir = tmp_path_factory.mktemp("jupyter")
    os.environ["JUPYTER_DATA_DIR"] = str(data_dir)
    from tlakit.kernel.install import KERNEL_NAME, install

    install(user=True)
    return KERNEL_NAME


def run(cells, kernel):
    nb = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(c) for c in cells]
    )
    NotebookClient(nb, timeout=180, kernel_name=kernel, allow_errors=True).execute()
    return nb


def outputs_of(cell):
    text = []
    for o in cell.get("outputs", []):
        if o.output_type == "stream":
            text.append(o.text)
        elif o.output_type == "error":
            text.append(o.evalue)
        else:
            data = o.get("data", {})
            text.append(data.get("text/html", "") or str(data.get("text/plain", "")))
    return "\n".join(text)


def test_a_bare_tla_cell_defines_and_parses_a_module(kernel):
    nb = run([MICROWAVE], kernel)
    assert "No error has been found" in outputs_of(nb.cells[0])


def test_a_bare_config_cell_checks_the_only_module(kernel):
    """No %tlc: prefix: with one module defined, the intent is unambiguous."""
    nb = run([MICROWAVE, "SPECIFICATION Spec\nINVARIANT Safety"], kernel)
    out = outputs_of(nb.cells[1])
    assert "Invariant violated" in out
    assert "radiation" in out


def test_python_still_runs_in_a_tla_notebook(kernel):
    """The reason to build on IPython rather than replace it."""
    nb = run(["import tlakit; print('v', tlakit.__version__)"], kernel)
    # Compared against the version under test, not a literal prefix. Hardcoding
    # "v 0.1" here made all 12 CI jobs fail on a release bump, which has nothing
    # to do with whether Python cells still run in a TLA+ notebook.
    assert f"v {tlakit.__version__}" in outputs_of(nb.cells[0])


def test_the_legacy_tlc_header_is_accepted(kernel):
    """kelvich/tlaplus_jupyter notebooks should keep working."""
    nb = run(
        [MICROWAVE, "%tlc:Microwave\nSPECIFICATION Spec\nINVARIANT Safety"], kernel
    )
    assert "Invariant violated" in outputs_of(nb.cells[1])


def test_a_config_with_no_module_explains_itself(kernel):
    nb = run(["SPECIFICATION Spec\nINVARIANT Safety"], kernel)
    assert "no module has been defined" in outputs_of(nb.cells[0])


# --------------------------------------------------------------------------
# Issue #35: completion and hover, over the real Jupyter message protocol.
#
# These do not go through NotebookClient, which only executes cells. A
# `complete_request` is a different message, and the whole claim being tested
# is that the kernel answers it in TLA+ rather than in Python -- so it has to
# be the real request, on a real kernel, over a real socket.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(kernel):
    from jupyter_client.manager import start_new_kernel

    manager, connection = start_new_kernel(kernel_name=kernel, startup_timeout=120)
    try:
        yield connection
    finally:
        connection.stop_channels()
        manager.shutdown_kernel(now=True)


def _reply(client, message_id):
    while True:
        reply = client.get_shell_msg(timeout=60)
        if reply["parent_header"].get("msg_id") == message_id:
            return reply["content"]


def complete(client, code, cursor_pos=None):
    cursor_pos = len(code) if cursor_pos is None else cursor_pos
    return _reply(client, client.complete(code, cursor_pos))


def inspect(client, code, cursor_pos):
    return _reply(client, client.inspect(code, cursor_pos))


def test_completion_in_a_tla_cell_answers_in_tla(client):
    """The whole point of the kernel over magics in a Python kernel: ask what
    `Su` completes to inside a module and the answer is `SubSeq`, an operator
    read out of tla2tools.jar -- not a Python name."""
    content = complete(client, "---- MODULE M ----\nEXTENDS Sequences\nSu")
    assert content["status"] == "ok"
    assert "SubSeq" in content["matches"]


def test_completion_sees_a_module_from_an_earlier_cell(client):
    """A session, not a cell: the module defined in one cell is in scope for
    completion in the next."""
    run_id = client.execute(MICROWAVE)
    _reply(client, run_id)
    content = complete(client, "---- MODULE Uses ----\nEXTENDS Microwave\nSaf")
    assert "Safety" in content["matches"]


def test_completion_in_a_python_cell_is_still_python(client):
    """Routing applies to completion too. A Python cell must not start
    offering TLA+ operators."""
    content = complete(client, "import tlakit\ntlakit.check_sou")
    assert any(m.endswith("check_source") for m in content["matches"]), content["matches"]


def test_hover_describes_a_standard_operator(client):
    code = "---- MODULE M ----\nEXTENDS Sequences\nX == Len(<<1>>)"
    content = inspect(client, code, code.index("Len") + 1)
    assert content["found"] is True
    text = content["data"]["text/plain"]
    assert "Len(s)" in text
    assert "length of sequence" in text


def test_hover_on_an_unknown_name_reports_not_found(client):
    code = "---- MODULE M ----\nX == NoSuchOperator"
    content = inspect(client, code, len(code) - 2)
    assert content["found"] is False
