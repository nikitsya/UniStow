BEGIN;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_payment_method_id TEXT,
    ADD COLUMN IF NOT EXISTS payment_method_saved_at TIMESTAMPTZ;

COMMIT;
