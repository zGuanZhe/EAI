# EAI Desktop derivation

This directory is a vendored snapshot of Sakana AI's AI Scientist v2 at commit
`96bd51617cfdbb494a9fc283af00fe090edfae48`.

EAI Desktop derives its Campaign stage and Journal model from the upstream
progressive tree search. Host execution, broad process cleanup, unmanaged
network access, and direct writes to EAI personal data are not exposed. Code
execution is routed through the EAI Docker approval broker.

The upstream `LICENSE` remains authoritative. Machine-generated manuscripts
must carry the disclosure required by that license.
