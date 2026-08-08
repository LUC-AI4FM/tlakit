---- MODULE broken_parse_error ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
\* A right-hand side is missing on purpose: this must never parse.
Next == x' =
Spec == Init /\ [][Next]_x
====
