---- MODULE broken_assumption_violation ----
EXTENDS Naturals
CONSTANT N
\* The config below pins N = 2, which never satisfies this.
ASSUME N > 10
VARIABLE x
Init == x = 0
Next == x' = x
Spec == Init /\ [][Next]_x
====
