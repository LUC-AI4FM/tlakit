---- MODULE broken_assertion_failed ----
EXTENDS Naturals, TLC
VARIABLE x
Init == x = 0
\* The Assert fires on the second step, when x is already 1.
Next == Assert(x = 0, "x must stay 0") /\ x' = x + 1
Spec == Init /\ [][Next]_x
====
