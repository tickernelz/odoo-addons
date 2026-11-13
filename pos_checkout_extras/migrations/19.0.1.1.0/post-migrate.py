def migrate(cr, version):
    cr.execute("""
        INSERT INTO pos_config_analytic_account_rel (config_id, account_id)
        SELECT DISTINCT config.id, account.id
          FROM pos_config config
         CROSS JOIN LATERAL jsonb_object_keys(config.analytic_distribution) AS distribution_key
         CROSS JOIN LATERAL unnest(string_to_array(distribution_key, ',')) AS account_key
          JOIN account_analytic_account account ON account.id::text = account_key
         WHERE jsonb_typeof(config.analytic_distribution) = 'object'
        ON CONFLICT DO NOTHING
    """)
