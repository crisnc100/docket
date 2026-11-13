# 0075: a neighbouring address wins the rules' proposed record

Document: [doc-0075-decoy_near_miss_address.pdf](../../corpus/seed-11/doc-0075-decoy_near_miss_address.pdf).
It gives `1110 Maple Streeet Apt 4`, tenant Michael Ryan, and the warning
`Do not confuse with neighbouring address: 1110 Maple Street Apt 20`.
The label expects review for a lease about `UNIT-0024`.

## What happened

The rules' proposed record is `UNIT-0040`, the neighbour.
Live, the model also suggests `UNIT-0040`. In the v0.1 simulation it suggested the
labelled record, `UNIT-0024`.
Docket gives score 0, tier none and reason `conflicting_records`; it goes to review.
No wrong automatic filing occurs.
The reviewer sees the same wrong record from both the rules and the model.

## Why

The matcher treats a literal neighbouring address as evidence even inside a warning.
It does not interpret negation.
The conflict gate correctly prevents automatic filing, but the rules' top pick is still wrong.
The live model reads the address the same way.

## What would fix it

The reviewer can choose `UNIT-0024` from the candidates on the existing page.
A rule that distinguishes the subject address from quoted or negated addresses might improve the proposed record.
It must retain conflicts when evidence really names two units.
That language-sensitive change is not built or measured.
