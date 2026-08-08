---- MODULE broken_temporal_violation ----
EXTENDS Naturals
VARIABLE x
Init == x = 0
Next == x' = 1 - x
Spec == Init /\ [][Next]_x
\* x only ever takes the values 0 and 1, so this can never hold.
Live == <>(x = 2)
====
