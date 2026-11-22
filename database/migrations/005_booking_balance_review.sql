BEGIN;

ALTER TABLE overdue_charges DROP CONSTRAINT overdue_charges_status_check;
ALTER TABLE overdue_charges ADD CONSTRAINT overdue_charges_status_check
    CHECK (status IN ('pending', 'paid', 'failed', 'waived', 'review'));

-- These outcomes were never verified declines; keep them pending review.
UPDATE overdue_charges
SET status = 'review'
WHERE status = 'failed'
  AND failure_reason IN ('Unconfirmed payment creation needs operator review.',
                         'Payment verification needs operator review.');

COMMIT;
