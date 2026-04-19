-- Migration script untuk menambahkan kolom Monte Carlo ke tabel signals
-- Jalankan dengan: psql -U postgres -d crypto_signals -f migrations/add_monte_carlo_columns.sql

-- Tambah kolom Monte Carlo jika belum ada
ALTER TABLE signals 
ADD COLUMN IF NOT EXISTS mc_prob_tp DECIMAL(5,1),
ADD COLUMN IF NOT EXISTS mc_prob_sl DECIMAL(5,1),
ADD COLUMN IF NOT EXISTS mc_prob_expire DECIMAL(5,1),
ADD COLUMN IF NOT EXISTS mc_confidence VARCHAR(10);

-- Tambah index untuk query yang lebih cepat (opsional)
CREATE INDEX IF NOT EXISTS idx_signals_mc_confidence 
ON signals(mc_confidence) WHERE mc_confidence IS NOT NULL;

-- Verifikasi kolom sudah ada
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'signals' 
  AND column_name LIKE 'mc_%'
ORDER BY column_name;
