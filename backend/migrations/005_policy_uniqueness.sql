CREATE UNIQUE INDEX IF NOT EXISTS idx_model_policies_firm_singleton ON model_policies(scope) WHERE scope='firm';
