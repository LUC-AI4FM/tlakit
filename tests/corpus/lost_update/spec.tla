---- MODULE LostUpdate ----
EXTENDS Naturals

VARIABLES
    counter,    \* the shared value
    pc,         \* where each thread is
    tmp         \* each thread's private copy

vars    == <<counter, pc, tmp>>
Threads == {"a", "b"}

Init ==
    /\ counter = 0
    /\ pc  = [t \in Threads |-> "read"]
    /\ tmp = [t \in Threads |-> 0]

\* Read the shared counter into a private variable.
Read(t) ==
    /\ pc[t] = "read"
    /\ tmp'  = [tmp EXCEPT ![t] = counter]
    /\ pc'   = [pc  EXCEPT ![t] = "write"]
    /\ UNCHANGED counter

\* Write back private + 1. Read and Write are separate steps, which is the
\* whole point: another thread can run in between.
Write(t) ==
    /\ pc[t] = "write"
    /\ counter' = tmp[t] + 1
    /\ pc'      = [pc EXCEPT ![t] = "done"]
    /\ UNCHANGED tmp

Done == \A t \in Threads : pc[t] = "done"

Next ==
    \/ \E t \in Threads : Read(t) \/ Write(t)
    \/ (Done /\ UNCHANGED vars)      \* stutter when finished, so no deadlock

Spec == Init /\ [][Next]_vars

\* Two threads each increment once, so the counter should end at 2.
Correct == Done => (counter = 2)
====
