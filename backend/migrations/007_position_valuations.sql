-- Explicit current valuation fields.  A cost basis is not a market value;
-- these columns are populated only by a confirmed, dated source observation.
ALTER TABLE positions ADD COLUMN market_value TEXT;
ALTER TABLE positions ADD COLUMN market_value_currency TEXT;
