-- Migration: Add highest_price and lowest_price columns for retroactive TP/SL tracking
-- Usage: psql -U postgres -d crypto_signals -f migrations/add_price_range_columns.sql

-- Add columns if they don't exist
ALTER TABLE signals 
ADD COLUMN IF NOT EXISTS highest_price DECIMAL(20, 8),
ADD COLUMN IF NOT EXISTS lowest_price DECIMAL(20, 8);

-- Create indexes for efficient retroactive TP/SL queries
CREATE INDEX IF NOT EXISTS idx_signals_highest_price 
ON signals(highest_price) WHERE highest_price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_signals_lowest_price 
ON signals(lowest_price) WHERE lowest_price IS NOT NULL;

-- Verify columns added
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'signals' 
  AND column_name IN ('highest_price', 'lowest_price')
ORDER BY column_name;