## f1 (medium)
Preserving every hyphen suppresses previously detected address conflicts. With filename `1010-Cedar-Street-Apt-1.pdf` and body `UNIT-0002\nTenant: Bianca Lee\nBuilding: CEDAR_01`, the base detects both records and forces review. HEAD loses the filename's address evidence, reports no conflict, and auto-files UNIT-0002 at 0.6500000.
Trigger: Submit filename `1010-Cedar-Street-Apt-1.pdf` with text `UNIT-0002\nTenant: Bianca Lee\nBuilding: CEDAR_01` using the shipped records and scoring config.
Evidence: 

## f2 (medium)
The address guard ignores letter-leading unit identifiers. For a configured address `1010 Cedar Street Apt A1`, input `1010 Cedar Street Apt B1` produces identical token lists containing only `1010`. The new fuzzy address strength is 0.9523809523809522; combined with the explicit record ID, this reaches the default filing threshold and auto-files despite the different unit. The base supplies zero address evidence and routes this input to review.
Trigger: Configure UNIT-0001 with address `1010 Cedar Street Apt A1` and no aliases, then submit `UNIT-0001\nAddress: 1010 Cedar Street Apt B1` using the default scoring config.
Evidence: 

