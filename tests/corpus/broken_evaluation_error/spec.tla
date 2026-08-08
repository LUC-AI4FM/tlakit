---- MODULE broken_evaluation_error ----
EXTENDS Naturals
VARIABLE x
\* F is defined on {0} only.
F == [i \in {0} |-> i]
Init == x = 0
\* F[1] is out of F's domain -- TLC must fail evaluating this, not TLC's
\* model of the world.
Next == x' = F[1]
Spec == Init /\ [][Next]_x
====
