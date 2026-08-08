---- MODULE broken_deadlock ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
\* Fires exactly once and then nothing is enabled -- no stuttering clause,
\* so TLC must report a deadlock rather than silently stopping.
Next == x = 0 /\ x' = 1
Spec == Init /\ [][Next]_x
====
