-- Migration 001: add nine lockers, enabling only Locker 4.
-- Run after schema.sql. Existing records are left unchanged on repeat runs.
BEGIN;

INSERT INTO lockers (number, size, price_per_day_cents, is_enabled)
VALUES (1, 'small', 100, FALSE),
       (2, 'small', 100, FALSE),
       (3, 'small', 100, FALSE),
       (4, 'small', 100, TRUE),
       (5, 'small', 100, FALSE),
       (6, 'small', 100, FALSE),
       (7, 'large', 150, FALSE),
       (8, 'large', 150, FALSE),
       (9, 'large', 150, FALSE) ON CONFLICT (number) DO NOTHING;

COMMIT;
