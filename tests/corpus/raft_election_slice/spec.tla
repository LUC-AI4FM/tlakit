---- MODULE RaftElectionSlice ----
EXTENDS Naturals, FiniteSets

Server == {"s1", "s2", "s3"}
Nil == "nil"
MaxTerm == 2

VARIABLES currentTerm, state, votedFor, votesGranted

TypeOK ==
    /\ currentTerm \in [Server -> 0..MaxTerm]
    /\ state \in [Server -> {"Follower", "Candidate", "Leader"}]
    /\ votedFor \in [Server -> Server \cup {Nil}]
    /\ votesGranted \in [Server -> SUBSET Server]

Init ==
    /\ currentTerm = [s \in Server |-> 0]
    /\ state = [s \in Server |-> "Follower"]
    /\ votedFor = [s \in Server |-> Nil]
    /\ votesGranted = [s \in Server |-> {}]

Timeout(s) ==
    /\ state[s] \in {"Follower", "Candidate"}
    /\ currentTerm[s] < MaxTerm
    /\ state' = [state EXCEPT ![s] = "Candidate"]
    /\ currentTerm' = [currentTerm EXCEPT ![s] = currentTerm[s] + 1]
    /\ votedFor' = [votedFor EXCEPT ![s] = s]
    /\ votesGranted' = [votesGranted EXCEPT ![s] = {s}]

RequestVote(voter, cand) ==
    /\ voter # cand
    /\ state[voter] = "Follower"
    /\ currentTerm[cand] > currentTerm[voter]
    /\ currentTerm' = [currentTerm EXCEPT ![voter] = currentTerm[cand]]
    /\ votedFor' = [votedFor EXCEPT ![voter] = cand]
    /\ votesGranted' = [votesGranted EXCEPT ![cand] = votesGranted[cand] \cup {voter}]
    /\ UNCHANGED state

BecomeLeader(cand) ==
    /\ state[cand] = "Candidate"
    /\ 2 * Cardinality(votesGranted[cand]) > Cardinality(Server)
    /\ state' = [state EXCEPT ![cand] = "Leader"]
    /\ UNCHANGED <<currentTerm, votedFor, votesGranted>>

\* Once the election settles (or the term bound is hit) nothing above stays
\* enabled; stutter rather than deadlock, same convention as LostUpdate.tla.
Terminating == UNCHANGED <<currentTerm, state, votedFor, votesGranted>>

Next ==
    \/ \E s \in Server : Timeout(s)
    \/ \E voter, cand \in Server : RequestVote(voter, cand)
    \/ \E s \in Server : BecomeLeader(s)
    \/ Terminating

Spec == Init /\ [][Next]_<<currentTerm, state, votedFor, votesGranted>>

\* At most one leader per term: the one safety property a leader-election
\* slice has to have, or nothing else about Raft can be trusted.
ElectionSafety ==
    \A s1, s2 \in Server :
        (state[s1] = "Leader" /\ state[s2] = "Leader" /\ currentTerm[s1] = currentTerm[s2])
        => s1 = s2
====
