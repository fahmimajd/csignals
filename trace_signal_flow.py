"""
trace_signal_flow.py
Jalankan: python trace_signal_flow.py
Durasi: 60 detik lalu keluar otomatis.

Menyuntikkan data sintetis ke pipeline untuk menemukan
di titik mana alur data terputus:

  WebSocket data
      ↓
  aggregator.update_state()     ← CHECK A
      ↓
  aggregator.compute_score()    ← CHECK B
      ↓
  confirmation.update()         ← CHECK C
      ↓
  db.save_signal()              ← CHECK D
      ↓
  DB INSERT                     ← CHECK E
"""

import asyncio
import asyncpg
import os
import sys
import time
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("TRACE")

# ── Tambahkan path modules ─────────────────────────────────────────────
sys.path.insert(0, ".")

TRACE = {}   # hasil setiap checkpoint


async def main():
    print("=" * 55)
    print("SIGNAL FLOW TRACER — 60 detik")
    print("=" * 55)

    # ── Setup DB pool ──────────────────────────────────────────────────
    try:
        pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "crypto_signals"),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
        )
        count_before = await pool.fetchval("SELECT COUNT(*) FROM signals")
        print(f"\nDB terhubung. Sinyal sebelum test: {count_before}")
    except Exception as e:
        print(f"\n❌ Gagal konek DB: {e}")
        print("Pastikan .env sudah benar dan PostgreSQL berjalan.")
        return

    # ── Import modules dari kode aktual yang berjalan ──────────────────
    print("\nMengimport modules...")
    try:
        import config
        TRACE["config"] = "✅ OK"
        print("  ✅ config.py")
    except Exception as e:
        TRACE["config"] = f"❌ {e}"
        print(f"  ❌ config.py: {e}")
        print_summary(pool, count_before)
        return

    try:
        from modules.aggregator import SignalAggregator
        agg = SignalAggregator()
        TRACE["aggregator_import"] = "✅ OK"
        print("  ✅ aggregator.py")
    except Exception as e:
        TRACE["aggregator_import"] = f"❌ {e}"
        print(f"  ❌ aggregator.py: {e}")
        print_summary(pool, count_before)
        return

    try:
        from modules.confirmation import SignalConfirmation
        try:
            conf = SignalConfirmation(config)
        except TypeError:
            conf = SignalConfirmation()
        TRACE["confirmation_import"] = "✅ OK"
        print("  ✅ confirmation.py")
    except Exception as e:
        TRACE["confirmation_import"] = f"❌ {e}"
        print(f"  ❌ confirmation.py: {e}")
        print_summary(pool, count_before)
        return

    try:
        from modules.database import Database
        db = Database()
        await db.initialize()
        TRACE["db_pool_inject"] = "✅ Pool di-inject dari luar"
        print("  ✅ database.py (pool initialized)")
    except Exception as e:
        TRACE["db_import"] = f"❌ {e}"
        print(f"  ❌ database.py: {e}")
        print_summary(pool, count_before)
        return

    # ══════════════════════════════════════════════════════════════════
    # CHECK A — update_state() bisa dipanggil
    # ══════════════════════════════════════════════════════════════════
    print("\n── CHECK A: aggregator.update_state() ──")
    try:
        agg.update_state("BTCUSDT",
            liq_long_usd=500_000,
            liq_short_usd=5_000_000,
            ob_imbalance=0.45,
            whale_buyers=5, whale_sellers=1,
            whale_buy_usd=3_000_000, whale_sell_usd=200_000,
            oi_change_pct=2.5, price_change_pct=0.8,
            taker_buy_pct=72.0, top_trader_long_pct=68.0,
        )
        TRACE["check_a"] = "✅ update_state() OK"
        print("  ✅ update_state() berhasil dipanggil")
    except Exception as e:
        TRACE["check_a"] = f"❌ {e}"
        print(f"  ❌ update_state() error: {e}")
        print("  → Signature method berubah setelah merge?")
        import traceback; traceback.print_exc()

    # ══════════════════════════════════════════════════════════════════
    # CHECK B — compute_score() menghasilkan nilai yang benar
    # ══════════════════════════════════════════════════════════════════
    print("\n── CHECK B: aggregator.compute_score() ──")
    try:
        result = agg.compute_score("BTCUSDT")

        # Handle berbagai kemungkinan return signature setelah merge
        if isinstance(result, tuple):
            score, components = result[0], result[1] if len(result) > 1 else []
        elif isinstance(result, (int, float)):
            score, components = int(result), []
            TRACE["check_b_warn"] = "⚠️ compute_score() hanya return int, bukan tuple"
        else:
            score, components = 0, []
            TRACE["check_b_warn"] = f"⚠️ Return type tidak dikenal: {type(result)}"

        TRACE["check_b_score"] = score
        TRACE["check_b_components"] = len(components)

        print(f"  Score: {score}")
        print(f"  Components: {len(components)}")
        print(f"  STRONG_THRESHOLD di config: {getattr(config, 'STRONG_THRESHOLD', 'TIDAK ADA')}")

        if abs(score) >= getattr(config, 'STRONG_THRESHOLD', 4):
            TRACE["check_b"] = f"✅ Score {score} MELEWATI threshold"
            print(f"  ✅ Score {score} → STRONG signal")
        else:
            TRACE["check_b"] = f"⚠️ Score {score} TIDAK melewati threshold"
            print(f"  ⚠️  Score {score} → di bawah threshold, sinyal tidak akan dikirim")
            print(f"  → Data sintetis seharusnya score ±6. Jika tidak, ada bug di compute_score()")

    except Exception as e:
        TRACE["check_b"] = f"❌ {e}"
        print(f"  ❌ compute_score() error: {e}")
        import traceback; traceback.print_exc()
        score = 0

    # ══════════════════════════════════════════════════════════════════
    # CHECK C — confirmation.update() menerima dan memproses sinyal
    # ══════════════════════════════════════════════════════════════════
    print("\n── CHECK C: confirmation.update() (sync method) ──")
    print("  Signature: update(symbol, score, current_time) → (confirmed, signal_type, id)")

    # Override CONFIRMATION_MINUTES ke nilai kecil untuk test cepat
    orig_minutes = getattr(config, 'CONFIRMATION_MINUTES', 3)
    try:
        config.CONFIRMATION_MINUTES = 0.05   # 3 detik
    except Exception:
        pass

    events_received = []
    conf_errors = []

    for i in range(4):
        try:
            # Method is SYNC — returns (confirmed, signal_type, signal_id)
            test_score = score if score != 0 else 5
            result = conf.update("BTCUSDT", test_score, current_time=time.time())

            if isinstance(result, tuple):
                confirmed, signal_type, signal_id = result[0], result[1] if len(result) > 1 else None, result[2] if len(result) > 2 else None
                if confirmed:
                    events_received.append((signal_type, signal_id))
                    print(f"  ✅ Iter {i+1}: CONFIRMED! type={signal_type}, id={signal_id}")
                else:
                    print(f"  ⏳ Iter {i+1}: Not confirmed yet (normal)")
            else:
                print(f"  ⏳ Iter {i+1}: Return: {result}")

        except Exception as e:
            conf_errors.append(f"Iter {i}: {e}")
            print(f"  ❌ Iter {i+1}: {e}")

        time.sleep(1.5)

    # Restore config
    try:
        config.CONFIRMATION_MINUTES = orig_minutes
    except Exception:
        pass

    if conf_errors:
        TRACE["check_c"] = f"❌ Errors: {conf_errors}"
        print(f"  ❌ confirmation.update() punya error")
    elif events_received:
        TRACE["check_c"] = f"✅ {len(events_received)} event diterima"
    else:
        TRACE["check_c"] = "⚠️ Tidak ada event — mungkin belum cukup candle/minutes"
        print("  ⚠️  Tidak ada confirmation — data sintetis cuma 1 update, butuh candle berturut")

        # Cek apakah ada cooldown aktif
        if hasattr(conf, '_cooldown_until'):
            cd = conf._cooldown_until.get("BTCUSDT", 0)
            if cd > time.time():
                remaining = (cd - time.time()) / 60
                TRACE["cooldown_stuck"] = f"⚠️ Cooldown aktif: sisa {remaining:.1f} menit"
                print(f"  ⚠️  BTCUSDT masih dalam cooldown: sisa {remaining:.1f} menit")

    # ══════════════════════════════════════════════════════════════════
    # CHECK D — db.save_signal() bisa menulis ke DB
    # ══════════════════════════════════════════════════════════════════
    print("\n── CHECK D: db.save_signal(symbol, signal_data) ──")
    print("  Signature: save_signal(symbol: str, signal_data: Dict) -> int")
    test_signal_data = {
        "signal_type": "STRONG_LONG",
        "score": 5,
        "entry_price": 95000.0,
        "stop_loss": 94000.0,
        "take_profit": 97000.0,
        "atr_value": 420.0,
        "rr_ratio": 2.0,
        "trail_trigger": 95420.0,
        "trail_distance": 315.0,
        "hold_hours": 6.0,
        "hold_deadline": None,
        "atr_factor": 1.0,
        "score_factor": 1.15,
        "volume_factor": 1.0,
        "volume_score": 1,
        "confirmed_at": time.time(),
    }

    try:
        signal_id = await db.save_signal("BTCUSDT_TEST", test_signal_data)
        if signal_id:
            TRACE["check_d"] = f"✅ save_signal() OK — id={signal_id}"
            print(f"  ✅ save_signal() berhasil — id={signal_id}")
            # Cleanup test data
            await pool.execute(
                "DELETE FROM signals WHERE symbol = 'BTCUSDT_TEST'"
            )
            print("  ✅ Test data dibersihkan")
        else:
            TRACE["check_d"] = "⚠️ save_signal() return None — mungkin dedup block"
            print("  ⚠️  save_signal() return None — sinyal diblok dedup?")
    except Exception as e:
        TRACE["check_d"] = f"❌ {e}"
        print(f"  ❌ save_signal() error: {e}")
        print("  → Kemungkinan: schema DB belum diupdate, atau pool tidak terhubung")
        import traceback; traceback.print_exc()

    # ══════════════════════════════════════════════════════════════════
    # CHECK E — Verifikasi DB setelah test
    # ══════════════════════════════════════════════════════════════════
    print("\n── CHECK E: Verifikasi DB ──")
    count_after = await pool.fetchval("SELECT COUNT(*) FROM signals")
    diff = count_after - count_before
    print(f"  Sinyal sebelum: {count_before}")
    print(f"  Sinyal sesudah: {count_after}")
    print(f"  Selisih       : {diff}")

    if diff == 0:
        TRACE["check_e"] = "⚠️ Tidak ada data baru — test signal tidak masuk DB"
    else:
        TRACE["check_e"] = f"✅ {diff} baris baru (termasuk test data yang sudah dibersihkan)"

    await pool.close()
    print_summary(pool, count_before)


def print_summary(pool, count_before):
    print("\n" + "=" * 55)
    print("HASIL TRACE")
    print("=" * 55)

    checkpoints = [
        ("config",              "Config loaded"),
        ("aggregator_import",   "aggregator.py import"),
        ("confirmation_import", "confirmation.py import"),
        ("db_pool_inject",      "DB pool sharing"),
        ("check_a",             "update_state()"),
        ("check_b",             "compute_score()"),
        ("check_c",             "confirmation.update()"),
        ("check_d",             "db.save_signal()"),
        ("check_e",             "DB write verified"),
    ]

    broken_at = None
    for key, label in checkpoints:
        val = TRACE.get(key, "— tidak dicek")
        icon = "✅" if val.startswith("✅") else ("⚠️ " if val.startswith("⚠️") else "❌")
        print(f"  {icon} {label}: {val}")
        if val.startswith("❌") and broken_at is None:
            broken_at = label

    print()
    if broken_at:
        print(f"🔴 ALUR TERPUTUS DI: {broken_at}")
        print("   Perbaiki bagian ini dulu sebelum yang lain.")
    elif "cooldown_stuck" in TRACE:
        print(f"🟡 MASALAH: {TRACE['cooldown_stuck']}")
        print("   Fix: Reset cooldown di DB atau restart bot dengan state bersih.")
    else:
        print("🟢 Semua checkpoint OK — paste hasil ini ke chat untuk analisis lebih lanjut.")

    print("\nPaste seluruh output ini ke chat.")


if __name__ == "__main__":
    asyncio.run(main())
