---- MODULE DieHard ----
EXTENDS Naturals

VARIABLES big, small

Min(a, b) == IF a < b THEN a ELSE b

Init == big = 0 /\ small = 0

FillSmall  == /\ small' = 3 /\ big' = big
FillBig    == /\ big' = 5 /\ small' = small
EmptySmall == /\ small' = 0 /\ big' = big
EmptyBig   == /\ big' = 0 /\ small' = small

SmallToBig ==
    LET poured == Min(small, 5 - big)
    IN /\ big' = big + poured
       /\ small' = small - poured

BigToSmall ==
    LET poured == Min(big, 3 - small)
    IN /\ small' = small + poured
       /\ big' = big - poured

Next == FillSmall \/ FillBig \/ EmptySmall \/ EmptyBig \/ SmallToBig \/ BigToSmall
Spec == Init /\ [][Next]_<<big, small>>

NotSolved == big # 4
====
