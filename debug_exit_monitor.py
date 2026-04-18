"""
debug_exit_monitor.py
Jalankan: python debug_exit_monitor.py
Tidak mengganggu bot yang sedang berjalan — read-only kecuali saat fix.

Mendiagnosis 6 kemungkinan penyebab exit monitor tidak update DB:
  1. Coroutine tidak di-gather di main.py
  2. Exception diam-diam (silenced exception)
  3. asyncpg pool tidak di-share (pool berbeda per modul)
  4. Query UPDATE salah kondisi WHERE
  5. Harga tidak pernah dicek ke API (loop tidak berjalan)
  6. hold_deadline NULL atau format salah di DB
"""

import asyncio
import asyncpg
import os
import time
import inspect
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "crypto_signals"),
    "user":     os.getenv("DB_USER", ""),
    "password": os.getenv("DB_PASSWORD", ""),
}

SEP = "─" * 55


async def run_all_diagnostics():
    print("=" * 55)
    print("EXIT MONITOR DIAGNOSTIC")
    print(f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    pool = await asyncpg.create_pool(**DB_CONFIG)

    results = {}

    results["db_state"]      = await check_db_state(pool)
    results["deadline"]      = await check_deadline_nulls(pool)
    results["price_check"]   = await check_price_never_fetched(pool)
    results["update_query"]  = await check_update_query_dry_run(pool)
    results["pool_sharing"]  = check_pool_sharing_hint()
    results["gather"]        = check_gather_hint()

    await pool.close()

    print_summary(results)


# ══════════════════════════════════════════════════════════════════════════
# CHECK 1 — State sinyal di DB
# ══════════════════════════════════════════════════════════════════════════
async def check_db_state(pool) -> dict:
    print(f"\n{SEP}")
    print("CHECK 1: State sinyal di DB")
    print(SEP)

    row = await pool.fetchrow("""
        SELECT
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE status = 'ACTIVE')      AS active,
            COUNT(*) FILTER (WHERE status = 'HIT_TP')      AS hit_tp,
            COUNT(*) FILTER (WHERE status = 'HIT_SL')      AS hit_sl,
            COUNT(*) FILTER (WHERE status = 'EXPIRED')     AS expired,
            COUNT(*) FILTER (WHERE status = 'CANCELLED')   AS cancelled,
            COUNT(*) FILTER (WHERE hold_deadline IS NULL)  AS null_deadline,
            COUNT(*) FILTER (
                WHERE hold_deadline IS NOT NULL
                AND hold_deadline < NOW()
                AND status = 'ACTIVE'
            )                                               AS overdue
        FROM signals
    """)

    d = dict(row)
    print(f"  Total sinyal      : {d['total']}")
    print(f"  ACTIVE            : {d['active']}")
    print(f"  HIT_TP            : {d['hit_tp']}")
    print(f"  HIT_SL            : {d['hit_sl']}")
    print(f"  EXPIRED           : {d['expired']}")
    print(f"  CANCELLED         : {d['cancelled']}")
    print(f"  hold_deadline NULL: {d['null_deadline']}")

    issue = None
    if d['active'] > 0 and d['hit_tp'] == 0 and d['hit_sl'] == 0 and d['expired'] == 0:
        issue = "CRITICAL: Semua sinyal ACTIVE, tidak ada yang resolved sama sekali"
        print(f"\n  ❌ {issue}")
    elif d['overdue'] > 0:
        issue = f"WARNING: {d['overdue']} sinyal sudah lewat deadline tapi masih ACTIVE"
        print(f"\n  ⚠️  {issue}")
    else:
        print(f"\n  ✅ Status distribusi terlihat normal")

    # Tampilkan sample sinyal overdue
    if d['overdue'] > 0:
        overdue_rows = await pool.fetch("""
            SELECT id, symbol, signal_type, hold_deadline, confirmed_at
            FROM signals
            WHERE hold_deadline IS NOT NULL
              AND hold_deadline < NOW()
              AND status = 'ACTIVE'
            ORDER BY hold_deadline ASC
            LIMIT 5
        """)
        print(f"\n  Sample sinyal overdue (harusnya sudah EXPIRED):")
        for r in overdue_rows:
            delta = datetime.now(timezone.utc) - r['hold_deadline'].replace(tzinfo=timezone.utc)
            print(f"    ID {r['id']} {r['symbol']} {r['signal_type']} "
                  f"— lewat {int(delta.total_seconds()/60)} menit")

    return {"status": "issue" if issue else "ok", "detail": issue, "data": d}


# ══════════════════════════════════════════════════════════════════════════
# CHECK 2 — hold_deadline NULL atau format salah
# ══════════════════════════════════════════════════════════════════════════
async def check_deadline_nulls(pool) -> dict:
    print(f"\n{SEP}")
    print("CHECK 2: hold_deadline NULL / format salah")
    print(SEP)

    # Cek berapa yang NULL
    null_count = await pool.fetchval("""
        SELECT COUNT(*) FROM signals
        WHERE status = 'ACTIVE' AND hold_deadline IS NULL
    """)

    # Cek tipe kolom
    col_type = await pool.fetchval("""
        SELECT data_type FROM information_schema.columns
        WHERE table_name = 'signals' AND column_name = 'hold_deadline'
    """)

    print(f"  Tipe kolom hold_deadline : {col_type}")
    print(f"  ACTIVE dengan NULL deadline: {null_count}")

    issue = None
    if col_type and "timestamp" not in col_type.lower():
        issue = f"Tipe kolom salah: {col_type} — harusnya TIMESTAMPTZ"
        print(f"  ❌ {issue}")
    elif null_count > 0:
        issue = f"{null_count} sinyal ACTIVE tidak punya deadline — exit monitor tidak tahu kapan expire"
        print(f"  ❌ {issue}")
        print(f"\n  FIX: Jalankan query berikut untuk set deadline retroaktif:")
        print(f"""
    UPDATE signals
    SET hold_deadline = confirmed_at + INTERVAL '6 hours'
    WHERE status = 'ACTIVE' AND hold_deadline IS NULL;
        """)
    else:
        print(f"  ✅ Semua sinyal ACTIVE punya deadline dengan tipe benar")

    return {"status": "issue" if issue else "ok", "detail": issue}


# ══════════════════════════════════════════════════════════════════════════
# CHECK 3 — Harga tidak pernah dicek (loop tidak berjalan)
# ══════════════════════════════════════════════════════════════════════════
async def check_price_never_fetched(pool) -> dict:
    print(f"\n{SEP}")
    print("CHECK 3: Apakah price check pernah berjalan?")
    print(SEP)

    # Cek apakah ada tabel log atau kolom last_checked
    has_log_table = await pool.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'monitor_log'
        )
    """)

    has_last_checked = await pool.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'signals'
              AND column_name = 'last_price_checked'
        )
    """)

    print(f"  Tabel monitor_log ada   : {'Ya' if has_log_table else 'Tidak'}")
    print(f"  Kolom last_price_checked: {'Ya' if has_last_checked else 'Tidak'}")

    issue = None
    if not has_log_table and not has_last_checked:
        issue = "Tidak ada log bahwa exit monitor pernah berjalan"
        print(f"\n  ⚠️  {issue}")
        print(f"  Saran: Tambahkan logging ke hold_duration_monitor()")
    else:
        if has_last_checked:
            # Cek kapan terakhir dicek
            last = await pool.fetchval("""
                SELECT MAX(last_price_checked) FROM signals
                WHERE status = 'ACTIVE'
            """)
            if last:
                age = datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)
                print(f"  Terakhir dicek: {int(age.total_seconds()/60)} menit lalu")
                if age.total_seconds() > 300:
                    issue = f"Price check terakhir {int(age.total_seconds()/60)} menit lalu — monitor mungkin mati"
                    print(f"  ❌ {issue}")
                else:
                    print(f"  ✅ Price check berjalan normal")

    return {"status": "issue" if issue else "ok", "detail": issue}


# ══════════════════════════════════════════════════════════════════════════
# CHECK 4 — Dry run UPDATE query
# ══════════════════════════════════════════════════════════════════════════
async def check_update_query_dry_run(pool) -> dict:
    print(f"\n{SEP}")
    print("CHECK 4: Dry run UPDATE query (rollback setelah cek)")
    print(SEP)

    # Ambil 1 sinyal ACTIVE yang sudah lama
    sample = await pool.fetchrow("""
        SELECT id, symbol, stop_loss, take_profit, entry_price
        FROM signals
        WHERE status = 'ACTIVE'
        ORDER BY confirmed_at ASC
        LIMIT 1
    """)

    if not sample:
        print("  ⚠️  Tidak ada sinyal ACTIVE untuk di-test")
        return {"status": "ok", "detail": "Tidak ada sinyal ACTIVE"}

    signal_id = sample['id']
    print(f"  Test dengan sinyal ID: {signal_id} ({sample['symbol']})")

    issue = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Simulasi update yang dilakukan exit monitor
            result = await conn.execute("""
                UPDATE signals
                SET status = 'EXPIRED',
                    closed_at = NOW(),
                    close_price = entry_price
                WHERE id = $1
                  AND status = 'ACTIVE'
            """, signal_id)

            # Cek berapa baris terupdate
            rows_affected = int(result.split()[-1])
            print(f"  Rows affected oleh UPDATE: {rows_affected}")

            # Verifikasi status berubah
            new_status = await conn.fetchval(
                "SELECT status FROM signals WHERE id = $1", signal_id
            )
            print(f"  Status setelah UPDATE (dalam transaksi): {new_status}")

            # ROLLBACK — jangan benar-benar ubah data
            raise Exception("ROLLBACK_INTENTIONAL")

    return {"status": "ok", "detail": None}  # tidak akan sampai sini


async def check_update_query_dry_run(pool) -> dict:
    print(f"\n{SEP}")
    print("CHECK 4: Dry run UPDATE query (rollback setelah cek)")
    print(SEP)

    sample = await pool.fetchrow("""
        SELECT id, symbol, stop_loss, take_profit, entry_price
        FROM signals WHERE status = 'ACTIVE'
        ORDER BY confirmed_at ASC LIMIT 1
    """)

    if not sample:
        print("  ⚠️  Tidak ada sinyal ACTIVE untuk di-test")
        return {"status": "ok", "detail": None}

    signal_id = sample['id']
    print(f"  Test sinyal ID: {signal_id} ({sample['symbol']})")

    # Gunakan savepoint agar bisa rollback parsial
    async with pool.acquire() as conn:
        try:
            await conn.execute("BEGIN")
            await conn.execute("SAVEPOINT sp_test")

            result = await conn.execute("""
                UPDATE signals
                SET status = 'EXPIRED', closed_at = NOW()
                WHERE id = $1 AND status = 'ACTIVE'
            """, signal_id)

            rows_affected = int(result.split()[-1])
            new_status = await conn.fetchval(
                "SELECT status FROM signals WHERE id = $1", signal_id
            )

            await conn.execute("ROLLBACK TO SAVEPOINT sp_test")
            await conn.execute("ROLLBACK")

            print(f"  Rows affected : {rows_affected}")
            print(f"  Status dalam transaksi: {new_status}")

            issue = None
            if rows_affected == 0:
                issue = "UPDATE tidak mengubah baris apapun — cek kondisi WHERE"
                print(f"  ❌ {issue}")
                print(f"  Kemungkinan: status sudah berubah, atau WHERE id salah")
            elif new_status != "EXPIRED":
                issue = f"Status tidak berubah: masih {new_status}"
                print(f"  ❌ {issue}")
            else:
                print(f"  ✅ Query UPDATE bekerja dengan benar (di-rollback)")

            return {"status": "issue" if issue else "ok", "detail": issue}

        except Exception as e:
            await conn.execute("ROLLBACK")
            print(f"  ❌ Error saat dry run: {e}")
            return {"status": "issue", "detail": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# CHECK 5 — Pool sharing hint (tidak bisa auto-detect, berikan panduan)
# ══════════════════════════════════════════════════════════════════════════
def check_pool_sharing_hint() -> dict:
    print(f"\n{SEP}")
    print("CHECK 5: Pool sharing (manual check)")
    print(SEP)

    print("""  Cek manual di kode kamu:

  ❌ SALAH — setiap modul buat pool sendiri:
     # liquidation.py
     pool = await asyncpg.create_pool(...)

     # hold_duration.py
     pool = await asyncpg.create_pool(...)   ← pool berbeda!

  ✅ BENAR — satu pool, di-inject ke semua modul:
     # main.py
     pool = await asyncpg.create_pool(...)
     db = DatabaseManager(pool)

     exit_monitor = ExitMonitor(db)    ← pakai db yang sama
     signal_gen   = SignalGen(db)      ← pakai db yang sama

  Jika setiap modul punya pool sendiri → UPDATE di modul A
  tidak terlihat di modul B karena transaksi terisolasi.""")

    return {"status": "manual", "detail": "Perlu cek manual di kode"}


# ══════════════════════════════════════════════════════════════════════════
# CHECK 6 — asyncio.gather hint
# ══════════════════════════════════════════════════════════════════════════
def check_gather_hint() -> dict:
    print(f"\n{SEP}")
    print("CHECK 6: asyncio.gather (manual check)")
    print(SEP)

    print("""  Cek di main.py apakah exit_monitor di-gather:

  ❌ SALAH — coroutine dibuat tapi tidak di-await:
     asyncio.create_task(ws_monitor())
     asyncio.create_task(display_loop())
     # exit_monitor() tidak dipanggil sama sekali!

  ❌ SALAH — di-await tapi sequential (bukan concurrent):
     await ws_monitor()      ← ini blocking, tidak pernah lanjut
     await exit_monitor()    ← tidak pernah sampai sini

  ✅ BENAR — semua di-gather concurrent:
     await asyncio.gather(
         ws_monitor(),
         exit_monitor(),     ← harus ada di sini
         display_loop(),
         return_exceptions=True   ← exception tidak kill semua task
     )

  ✅ ATAU pakai create_task agar bisa cancel individual:
     tasks = [
         asyncio.create_task(ws_monitor()),
         asyncio.create_task(exit_monitor()),
         asyncio.create_task(display_loop()),
     ]
     await asyncio.gather(*tasks, return_exceptions=True)""")

    return {"status": "manual", "detail": "Perlu cek manual di kode"}


# ══════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════
def print_summary(results: dict):
    print(f"\n{'=' * 55}")
    print("RINGKASAN DIAGNOSIS")
    print("=" * 55)

    checks = {
        "db_state":    "State sinyal di DB",
        "deadline":    "hold_deadline NULL/format",
        "price_check": "Price check pernah berjalan",
        "update_query":"UPDATE query dry run",
        "pool_sharing":"Pool sharing",
        "gather":      "asyncio.gather",
    }

    issues_found = []
    for key, label in checks.items():
        r = results.get(key, {})
        status = r.get("status", "ok")
        detail = r.get("detail", "")
        if status == "issue":
            print(f"  ❌ {label}: {detail}")
            issues_found.append(label)
        elif status == "manual":
            print(f"  👁  {label}: Cek manual diperlukan")
        else:
            print(f"  ✅ {label}: OK")

    print()
    if issues_found:
        print(f"Ditemukan {len(issues_found)} masalah otomatis:")
        for i, issue in enumerate(issues_found, 1):
            print(f"  {i}. {issue}")
        print("\nPerbaiki dari atas ke bawah — biasanya 1 masalah")
        print("yang menyebabkan semua sisanya.")
    else:
        print("Tidak ada masalah terdeteksi secara otomatis.")
        print("Fokus ke CHECK 5 (pool sharing) dan CHECK 6 (gather)")
        print("karena keduanya perlu dicek manual di kode.")

    print(f"\n{'=' * 55}")
    print("LANGKAH SELANJUTNYA")
    print("=" * 55)
    print("""
Jika masalah belum ketemu setelah diagnostic ini:

1. Tambahkan logging eksplisit di exit monitor:
   logger.info(f"[EXIT] Cek {len(signals)} sinyal aktif")
   logger.info(f"[EXIT] {symbol} current={price} tp={tp} sl={sl}")
   logger.info(f"[EXIT] UPDATE result: {result}")

2. Jalankan exit monitor standalone (tanpa main.py):
   asyncio.run(exit_monitor_standalone())
   Lihat apakah ada exception yang selama ini diam.

3. Cek PostgreSQL log:
   tail -f /var/log/postgresql/postgresql-*.log
   Lihat apakah ada UPDATE yang masuk ke DB.

4. Jika masih tidak ketemu, paste isi hold_duration.py
   dan main.py ke chat ini — saya bantu debug langsung.
""")


if __name__ == "__main__":
    asyncio.run(run_all_diagnostics())
