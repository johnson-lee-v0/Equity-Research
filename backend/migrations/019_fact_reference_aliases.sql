CREATE TABLE IF NOT EXISTS output_claim_aliases (
    output_id TEXT NOT NULL REFERENCES outputs(id),
    claim_index INTEGER NOT NULL,
    claim_id TEXT NOT NULL,
    fact_id TEXT NOT NULL REFERENCES fact_claims(id),
    namespace TEXT NOT NULL,
    PRIMARY KEY(output_id, claim_index)
);
CREATE UNIQUE INDEX IF NOT EXISTS output_claim_alias_lookup ON output_claim_aliases(output_id, claim_id);
