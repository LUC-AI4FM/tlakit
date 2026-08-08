---- MODULE Microwave ----

VARIABLES doorOpen, heating

TypeOK == doorOpen \in BOOLEAN /\ heating \in BOOLEAN

Init == doorOpen = TRUE /\ heating = FALSE

OpenDoor ==
    /\ doorOpen = FALSE
    /\ doorOpen' = TRUE
    /\ heating' = FALSE

CloseDoor ==
    /\ doorOpen = TRUE
    /\ doorOpen' = FALSE
    /\ UNCHANGED heating

StartHeat ==
    /\ doorOpen = FALSE
    /\ heating = FALSE
    /\ heating' = TRUE
    /\ UNCHANGED doorOpen

StopHeat ==
    /\ heating = TRUE
    /\ heating' = FALSE
    /\ UNCHANGED doorOpen

Next == OpenDoor \/ CloseDoor \/ StartHeat \/ StopHeat
Spec == Init /\ [][Next]_<<doorOpen, heating>>

\* The whole point of the spec: the microwave can never heat with the door open.
SafetyInvariant == heating => ~doorOpen
====
