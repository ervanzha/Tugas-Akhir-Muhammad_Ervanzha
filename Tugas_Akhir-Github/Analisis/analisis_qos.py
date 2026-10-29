#!/usr/bin/env python3
"""
Analisis Data Pengujian QoS Micro-ROS ESP32
============================================
Membaca semua CSV skenario (R-Q5-P112.csv, BE-Q20-P512.csv, dst) di satu
folder, lalu membuat tabel ringkasan dan grafik untuk Bab IV.

Aturan pengolahan data:
 1. Jendela analisis = periode saat SEMUA node aktif bersamaan
    (dari node terakhir mulai sampai node pertama berhenti).
 2. Packet loss dihitung per node di dalam jendela:
    dikirim  = seq terakhir - seq pertama + 1
    diterima = jumlah seq unik
 3. Latency dilaporkan apa adanya (t_receive - t_send) tanpa koreksi.
    Sisa selisih jam NTP antar ESP32 (orde +-10 ms) bersifat acak antar
    node, sehingga pada nilai gabungan 7 node efeknya saling meniadakan.
    Koreksi per node TIDAK dipakai karena dapat ikut menghapus antrian
    (queuing delay) yang nyata pada node yang sedang padat.
    Persentase paket dengan latency negatif dilaporkan sebagai indikator
    ketidakpastian sinkronisasi waktu.

Skenario tanpa paket diterima:
 CSV yang hanya berisi header (tidak ada satu paket pun yang sampai,
 misalnya Best Effort dengan payload > MTU 512 B) TETAP dimasukkan ke
 ringkasan sebagai packet loss 100%. Kolom latency diisi kosong (-) dan
 kolom Keterangan menjelaskan penyebabnya.

Cara pakai (Ubuntu):
  python3 analisis_qos.py                  -> baca /media/sf_skripsi_van
  python3 analisis_qos.py /path/folder     -> baca folder lain

Library (sekali install):
  sudo apt install -y python3-pandas python3-matplotlib python3-openpyxl

Output: <folder>/hasil_analisis/
  ringkasan_hasil.xlsx   sheet "Per Skenario", "Per Node", "Tabel Silang"
  ringkasan_hasil.csv, ringkasan_per_node.csv, tabel_silang.txt
  GRAFIK UTAMA BAB IV (batang, sumbu-x payload, warna = queue depth):
    grafik_latensi_reliable.png      grafik_latensi_besteffort.png
    grafik_packetloss_reliable.png   grafik_packetloss_besteffort.png
    grafik_lajukirim_reliable.png    grafik_lajukirim_besteffort.png  (pendukung)
  latency_vs_waktu/      satu grafik per skenario
  grafik_tambahan/       grafik garis (cara baca alternatif)
  lampiran/              grafik bersumbu-x kode skenario + boxplot
"""

import glob
import os
import re
import sys

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker

FOLDER = sys.argv[1] if len(sys.argv) > 1 else '/media/sf_skripsi_van'
OUT = os.path.join(FOLDER, 'hasil_analisis')
KOREKSI_OFFSET = False   # lihat aturan no. 3

POLA = re.compile(r'^(R|BE)-Q(\d+)-P(\d+)$')
QOS_NAMA = {'R': 'Reliable', 'BE': 'Best Effort'}
URUT_QOS = ['Reliable', 'Best Effort']
WARNA_PAYLOAD = {112: '#2a78b5', 512: '#d9822b', 1024: '#3a9a5b'}
GAYA_PAYLOAD = {112: dict(marker='o', ms=6, ls='-'),
                512: dict(marker='s', ms=10, ls='-'),
                1024: dict(marker='^', ms=6, ls='--')}
WARNA_QOS = {'Reliable': '#2a78b5', 'Best Effort': '#d9822b'}

plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Liberation Serif', 'Times New Roman', 'DejaVu Serif'], 'font.size': 10,
                     'axes.grid': True, 'grid.alpha': 0.3,
                     'axes.spines.top': False, 'axes.spines.right': False})


# ---------------------------------------------------------------- baca data
def baca_semua():
    data, kosong = [], []
    for path in sorted(glob.glob(os.path.join(FOLDER, '*.csv'))):
        nama = os.path.splitext(os.path.basename(path))[0]
        m = POLA.match(nama)
        if not m:
            continue
        info = {'skenario': nama, 'qos': QOS_NAMA[m.group(1)],
                'queue': int(m.group(2)), 'payload': int(m.group(3))}
        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            print(f'  [LEWATI] {nama}: file benar-benar kosong (tanpa header)')
            continue
        if 'node_id' not in df.columns:
            print(f'  [LEWATI] {nama}: format lama (kolom tidak sesuai)')
            continue
        if df.empty:
            kosong.append(info)
            print(f'  [0 PAKET] {nama}: tidak ada paket diterima '
                  f'-> dicatat packet loss 100%')
            continue
        for k, v in info.items():
            df[k] = v
        data.append(df)
        print(f'  [OK] {nama}: {len(df):,} baris')
    if not data and not kosong:
        print('Tidak ada CSV skenario yang valid di folder ini.')
        sys.exit(1)
    df_all = pd.concat(data, ignore_index=True) if data else pd.DataFrame()
    return df_all, kosong


def potong_jendela(g):
    """Ambil hanya periode saat semua node aktif bersamaan."""
    mulai = g.groupby('node_id')['t_receive_ms'].min().max()
    selesai = g.groupby('node_id')['t_receive_ms'].max().min()
    g = g[(g['t_receive_ms'] >= mulai) & (g['t_receive_ms'] <= selesai)].copy()
    return g, (selesai - mulai) / 1000.0


def koreksi_offset(g):
    g = g.drop_duplicates(['node_id', 'seq_num']).copy()
    g['latency_raw'] = g['latency_ms']
    if KOREKSI_OFFSET:
        p1 = g.groupby('node_id')['latency_raw'].quantile(0.01)
        acuan = p1.median()
        g['offset'] = g['node_id'].map(p1 - acuan)
        g['latency_ms'] = g['latency_raw'] - g['offset']
    else:
        g['offset'] = 0.0
    return g


# ---------------------------------------------------------------- ringkasan
def baris_kosong(info):
    """Baris ringkasan untuk skenario tanpa satu pun paket diterima."""
    nan = float('nan')
    ket = 'Tidak ada paket diterima'
    if info['qos'] == 'Best Effort' and info['payload'] > 500:
        ket += ' (pesan > MTU 512 B, Best Effort tanpa fragmentasi)'
    return {
        'Skenario': info['skenario'], 'QoS': info['qos'],
        'Queue': info['queue'], 'Payload (Byte)': info['payload'],
        'Jumlah Node': 0, 'Durasi Analisis (s)': nan,
        'Paket Dikirim': nan, 'Paket Diterima': 0, 'Paket Hilang': nan,
        'Packet Loss (%)': 100.0,
        'Laju Kirim per Node (paket/s)': nan,
        'Latency Min (ms)': nan, 'Latency Rata-rata (ms)': nan,
        'Latency Median (ms)': nan, 'Std Deviasi (ms)': nan,
        'Latency P95 (ms)': nan, 'Latency Max (ms)': nan,
        'Paket Latency Negatif (%)': nan,
        'Keterangan': ket,
    }


def ringkas(df, kosong):
    per_sken, per_node, bersih = [], [], []
    groups = df.groupby('skenario', sort=False) if not df.empty else []
    for sken, g in groups:
        info = g.iloc[0]
        g, durasi = potong_jendela(g)
        g = koreksi_offset(g)
        bersih.append(g)

        tx_tot = rx_tot = 0
        for nid, gn in g.groupby('node_id'):
            tx = int(gn['seq_num'].max() - gn['seq_num'].min() + 1)
            rx = int(gn['seq_num'].nunique())
            tx_tot += tx
            rx_tot += rx
            per_node.append({
                'Skenario': sken, 'Node': int(nid),
                'Dikirim': tx, 'Diterima': rx, 'Hilang': tx - rx,
                'Packet Loss (%)': round((tx - rx) / tx * 100, 3),
                'Laju Kirim (paket/s)': round(tx / durasi, 2),
                'Latency Min (ms)': round(gn['latency_ms'].min(), 2),
                'Latency Rata-rata (ms)': round(gn['latency_ms'].mean(), 2),
                'Latency Median (ms)': round(gn['latency_ms'].median(), 2),
                'Latency Max (ms)': round(gn['latency_ms'].max(), 2),
            })

        lat = g['latency_ms']
        per_sken.append({
            'Skenario': sken, 'QoS': info['qos'], 'Queue': info['queue'],
            'Payload (Byte)': info['payload'],
            'Jumlah Node': g['node_id'].nunique(),
            'Durasi Analisis (s)': round(durasi),
            'Paket Dikirim': tx_tot, 'Paket Diterima': rx_tot,
            'Paket Hilang': tx_tot - rx_tot,
            'Packet Loss (%)': round((tx_tot - rx_tot) / tx_tot * 100, 3),
            'Laju Kirim per Node (paket/s)':
                round(tx_tot / g['node_id'].nunique() / durasi, 2),
            'Latency Min (ms)': round(lat.min(), 2),
            'Latency Rata-rata (ms)': round(lat.mean(), 2),
            'Latency Median (ms)': round(lat.median(), 2),
            'Std Deviasi (ms)': round(lat.std(), 2),
            'Latency P95 (ms)': round(lat.quantile(0.95), 2),
            'Latency Max (ms)': round(lat.max(), 2),
            'Paket Latency Negatif (%)':
                round((lat < 0).mean() * 100, 2),
            'Keterangan': ('' if g['node_id'].nunique() == 7 else
                           f'Hanya {g["node_id"].nunique()} node aktif'),
        })

    for info in kosong:
        per_sken.append(baris_kosong(info))

    rs = pd.DataFrame(per_sken)
    rs['_q'] = rs['QoS'].map({q: i for i, q in enumerate(URUT_QOS)})
    rs = rs.sort_values(['_q', 'Queue', 'Payload (Byte)']).drop(columns='_q')
    rs.insert(0, 'No', range(1, len(rs) + 1))
    for k in ['Jumlah Node', 'Paket Dikirim', 'Paket Diterima', 'Paket Hilang']:
        rs[k] = rs[k].astype('Int64')
    rs['Durasi Analisis (s)'] = rs['Durasi Analisis (s)'].round().astype('Int64')
    bersih = (pd.concat(bersih, ignore_index=True) if bersih
              else pd.DataFrame(columns=['skenario', 'node_id',
                                         't_receive_ms', 'latency_ms']))
    return rs, pd.DataFrame(per_node), bersih


# ---------------------------------------------------------------- grafik
SUBFOLDER = ''   # diubah di main() untuk grafik tambahan / lampiran


def simpan(fig, nama, dpi=150):
    folder = os.path.join(OUT, SUBFOLDER) if SUBFOLDER else OUT
    os.makedirs(folder, exist_ok=True)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, nama), dpi=dpi)
    plt.close(fig)


def _angka(v, desimal=2, akhiran=''):
    """Format angka gaya Indonesia: 10,20 / 0,025% / 100%."""
    if v >= 1 and abs(v - round(v)) < 1e-9 and akhiran == '%':
        return f'{int(round(v))}{akhiran}'
    return f'{v:.{desimal}f}'.replace('.', ',') + akhiran


def grafik_batang_kelompok(rs, q, kolom, ylabel, judul, nama, desimal=2, akhiran='',
                           garis_target=None):
    """GRAFIK UTAMA BAB IV.

    Satu kebijakan reliability per gambar. Sumbu-x = ukuran payload (byte),
    tiga batang per payload = queue depth 5 / 20 / 100 (dibedakan warna).
    Setiap batang = satu skenario. Nilai kosong (0 paket) diberi tulisan.
    """
    sub = rs[rs['QoS'] == q]
    if sub.empty:
        return
    payloads = sorted(rs['Payload (Byte)'].unique())
    queues = sorted(rs['Queue'].unique())
    lebar = 0.8 / len(queues)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ymax = sub[kolom].max(skipna=True)
    ymax = 1 if pd.isna(ymax) or ymax <= 0 else ymax
    for k, qd in enumerate(queues):
        vals = []
        for p in payloads:
            baris = sub[(sub['Queue'] == qd) & (sub['Payload (Byte)'] == p)]
            vals.append(baris[kolom].iloc[0] if not baris.empty else float('nan'))
        xs = [i + (k - (len(queues) - 1) / 2) * lebar for i in range(len(payloads))]
        ax.bar(xs, [0 if pd.isna(v) else v for v in vals], lebar,
               color=WARNA_QUEUE.get(qd), edgecolor='black', linewidth=0.6,
               label=f'{qd} pesan')
        for x, v in zip(xs, vals):
            if pd.isna(v):
                ax.text(x, ymax * 0.02, '0 paket\nditerima', ha='center', va='bottom',
                        fontsize=7, color='#555555', rotation=90)
            else:
                ax.text(x, v + ymax * 0.015, _angka(v, desimal, akhiran),
                        ha='center', va='bottom', fontsize=8)
    if garis_target is not None:
        ax.axhline(garis_target, color='black', lw=0.8, ls='--')
        ax.text(len(payloads) - 0.55, garis_target, f' target {garis_target:g}',
                va='bottom', ha='right', fontsize=8, style='italic')
    ax.set_ylim(0, ymax * 1.15)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda y, _: f'{y:g}'.replace('.', ',')))
    ax.set_xticks(range(len(payloads)))
    ax.set_xticklabels([f'{p:,}'.replace(',', '.') for p in payloads])
    ax.set_xlabel('Ukuran payload (byte)')
    ax.set_ylabel(ylabel)
    ax.set_title(f'{judul} ({q})', fontweight='bold')
    ax.legend(title='Queue depth', frameon=False, loc='upper left',
              bbox_to_anchor=(1.0, 1.0))
    ax.grid(axis='x', visible=False)
    simpan(fig, nama, dpi=200)


def tabel_silang(rs, kolom):
    """Tabel baris = queue depth, kolom = payload, per kebijakan reliability."""
    blok = []
    for q in URUT_QOS:
        sub = rs[rs['QoS'] == q]
        if sub.empty:
            continue
        t = sub.pivot(index='Queue', columns='Payload (Byte)', values=kolom)
        t.index = [f'{q} | Queue {v}' for v in t.index]
        t.columns = [f'{c} byte' for c in t.columns]
        blok.append(t)
    return pd.concat(blok) if blok else pd.DataFrame()


LABEL_SUMBU = {'Queue': ('Queue depth (pesan)', 'Queue depth'),
               'Payload (Byte)': ('Ukuran payload (byte)', 'Payload')}
WARNA_QUEUE = {5: '#2a78b5', 20: '#d9822b', 100: '#3a9a5b'}
GAYA_QUEUE = {5: dict(marker='o', ms=6, ls='-'),
              20: dict(marker='s', ms=8, ls='-'),
              100: dict(marker='^', ms=6, ls='--')}


def _gambar_panel(ax, rs, q, kolom, sumbu):
    """Gambar satu kebijakan reliability pada satu sumbu (9 skenario)."""
    lain = 'Payload (Byte)' if sumbu == 'Queue' else 'Queue'
    _, judul_legend = LABEL_SUMBU[lain]
    satuan_legend = 'byte' if lain == 'Payload (Byte)' else 'pesan'
    warna = WARNA_PAYLOAD if lain == 'Payload (Byte)' else WARNA_QUEUE
    gaya = GAYA_PAYLOAD if lain == 'Payload (Byte)' else GAYA_QUEUE
    nilai_x = sorted(rs[sumbu].unique())
    posisi = {v: i for i, v in enumerate(nilai_x)}
    sub = rs[rs['QoS'] == q]
    # posisi label angka per garis agar tidak saling menimpa
    OFFS = [((-9, 0), 'right', 'center'), ((9, 0), 'left', 'center'), ((0, 8), 'center', 'bottom')]
    seri_kosong = []
    for k, (v, gp) in enumerate(sub.groupby(lain)):
        gp = gp.sort_values(sumbu)
        if gp[kolom].isna().all():
            seri_kosong.append(f'{v} {satuan_legend}')
            continue
        x = gp[sumbu].map(posisi)
        ax.plot(x, gp[kolom], lw=2, color=warna.get(v),
                label=f'{v} {satuan_legend}', **gaya.get(v, {'marker': 'o'}))
        off, ha, va = OFFS[k % len(OFFS)]
        for xi, yi in zip(x, gp[kolom]):
            if pd.isna(yi):
                continue
            ax.annotate(f'{yi:.2f}', (xi, yi), textcoords='offset points',
                        xytext=off, ha=ha, va=va, fontsize=8)
    # titik x yang tidak punya data sama sekali (semua garis kosong)
    for xv in nilai_x:
        if sub[sub[sumbu] == xv][kolom].isna().all() and not sub[sub[sumbu] == xv].empty:
            ax.annotate('0 paket\nditerima', (posisi[xv], 0), xycoords=('data', 'axes fraction'),
                        textcoords='offset points', xytext=(0, 8), ha='center',
                        fontsize=8, color='#888888')
    if seri_kosong and len(seri_kosong) < sub[lain].nunique():
        ax.text(0.02, 0.97, f'{judul_legend} ' + ' dan '.join(seri_kosong) +
                ': 0 paket diterima', transform=ax.transAxes, fontsize=8,
                color='#555555', style='italic', va='top')
    ax.margins(y=0.15)
    ax.set_xticks(range(len(nilai_x)))
    ax.set_xticklabels([f'{v:,}'.replace(',', '.') for v in nilai_x])
    ax.set_xlim(-0.55, len(nilai_x) - 0.45)
    ax.set_xlabel(LABEL_SUMBU[sumbu][0])
    ax.legend(title=judul_legend, frameon=False)


def grafik_faktor(rs, kolom, ylabel, judul, nama, sumbu):
    """Grafik pengaruh SATU faktor.

    Sumbu-y = besaran yang diukur, sumbu-x = faktor yang diamati (queue depth
    ATAU payload, lengkap dengan satuannya). Setiap titik adalah SATU skenario:
    posisinya di sumbu-x menunjukkan nilai faktor tersebut, warna garis
    menunjukkan nilai faktor lainnya.

    Menghasilkan:
      <nama>.png                    dua panel (Reliable | Best Effort)
      <nama>_reliable.png           grafik Reliable saja
      <nama>_besteffort.png         grafik Best Effort saja
    """
    qos_ada = [q for q in URUT_QOS if q in rs['QoS'].values]
    fig, axes = plt.subplots(1, len(qos_ada), figsize=(5.2 * len(qos_ada), 3.8),
                             sharey=True, squeeze=False)
    for ax, q in zip(axes[0], qos_ada):
        _gambar_panel(ax, rs, q, kolom, sumbu)
        ax.set_title(q)
    axes[0][0].set_ylabel(ylabel)
    fig.suptitle(judul, fontweight='bold')
    simpan(fig, nama)

    for q in qos_ada:
        fig, ax = plt.subplots(figsize=(6, 4))
        _gambar_panel(ax, rs, q, kolom, sumbu)
        ax.set_ylabel(ylabel)
        ax.set_title(f'{judul} ({q})', fontweight='bold')
        akhiran = q.lower().replace(' ', '')
        simpan(fig, nama.replace('.png', f'_{akhiran}.png'))


def grafik_reliability(rs, kolom, ylabel, judul, nama):
    """Perbandingan Reliable vs Best Effort.

    Satu panel per queue depth, sumbu-x ukuran payload (byte), dua batang
    (Reliable dan Best Effort) untuk setiap payload. Queue dan payload
    ditahan sama di setiap pasangan batang.
    """
    queues = sorted(rs['Queue'].unique())
    payloads = sorted(rs['Payload (Byte)'].unique())
    qos_ada = [q for q in URUT_QOS if q in rs['QoS'].values]
    lebar = 0.8 / max(len(qos_ada), 1)
    fig, axes = plt.subplots(1, len(queues), figsize=(4.2 * len(queues), 3.8),
                             sharey=True, squeeze=False)
    for ax, qd in zip(axes[0], queues):
        for k, q in enumerate(qos_ada):
            sub = rs[(rs['Queue'] == qd) & (rs['QoS'] == q)].set_index('Payload (Byte)')
            vals = [sub[kolom].get(p, float('nan')) for p in payloads]
            xs = [i + (k - (len(qos_ada) - 1) / 2) * lebar for i in range(len(payloads))]
            bars = ax.bar(xs, [0 if pd.isna(v) else v for v in vals], lebar,
                          color=WARNA_QOS[q], label=q)
            ax.bar_label(bars, labels=['0 paket' if pd.isna(v) else f'{v:.2f}'
                                       for v in vals], fontsize=7)
        ax.set_title(f'Queue depth {qd}')
        ax.set_xticks(range(len(payloads)))
        ax.set_xticklabels([f'{p:,}'.replace(',', '.') for p in payloads])
        ax.set_xlabel('Ukuran payload (byte)')
    axes[0][0].set_ylabel(ylabel)
    axes[0][-1].legend(title='Reliability', frameon=False)
    fig.suptitle(judul, fontweight='bold')
    simpan(fig, nama)


def grafik_batang(rs, kolom, ylabel, judul, nama):
    fig, ax = plt.subplots(figsize=(max(6, len(rs) * 0.6), 4))
    nilai = rs[kolom]
    bars = ax.bar(rs['Skenario'], nilai.fillna(0),
                  color=rs['QoS'].map(WARNA_QOS))
    ax.bar_label(bars, labels=['0 paket' if pd.isna(v) else f'{v:.2f}'
                               for v in nilai], fontsize=7)
    ax.set_ylabel(ylabel)
    ax.set_title(judul, fontweight='bold')
    plt.setp(ax.get_xticklabels(), rotation=60, ha='right')
    simpan(fig, nama)


def grafik_boxplot(bersih, rs, nama):
    urut = [s for s in rs['Skenario'] if (bersih['skenario'] == s).any()]
    if not urut:
        return
    isi = [bersih.loc[bersih['skenario'] == s, 'latency_ms'].values for s in urut]
    fig, ax = plt.subplots(figsize=(max(6, len(urut) * 0.6), 4))
    ax.boxplot(isi, showfliers=False)
    ax.set_xticks(range(1, len(urut) + 1))
    ax.set_xticklabels(urut, rotation=60, ha='right')
    ax.set_ylabel('Latency (ms)')
    ax.set_title('Distribusi latency per skenario (tanpa outlier)',
                 fontweight='bold')
    simpan(fig, nama)


def grafik_waktu(bersih):
    folder = os.path.join(OUT, 'latency_vs_waktu')
    os.makedirs(folder, exist_ok=True)
    for sken, g in bersih.groupby('skenario'):
        t0 = g['t_receive_ms'].min()
        fig, ax = plt.subplots(figsize=(8, 3.5))
        for nid, gn in g.groupby('node_id'):
            ax.plot((gn['t_receive_ms'] - t0) / 1000, gn['latency_ms'],
                    lw=0.5, label=f'Node {nid}')
        ax.set_xlabel('Waktu (detik)')
        ax.set_ylabel('Latency (ms)')
        ax.set_title(f'Latency terhadap waktu, {sken}', fontweight='bold')
        ax.legend(fontsize=7, ncol=7, loc='upper right', frameon=False)
        fig.tight_layout()
        fig.savefig(os.path.join(folder, f'{sken}.png'), dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    print(f'Membaca data dari: {FOLDER}')
    df, kosong = baca_semua()
    os.makedirs(OUT, exist_ok=True)
    rs, rn, bersih = ringkas(df, kosong)

    rs.to_csv(os.path.join(OUT, 'ringkasan_hasil.csv'), index=False, na_rep='-')
    rn.to_csv(os.path.join(OUT, 'ringkasan_per_node.csv'), index=False)
    try:
        with pd.ExcelWriter(os.path.join(OUT, 'ringkasan_hasil.xlsx')) as xw:
            rs.to_excel(xw, sheet_name='Per Skenario', index=False, na_rep='-')
            rn.to_excel(xw, sheet_name='Per Node', index=False)
    except ImportError:
        print('  (openpyxl belum terinstall, file .xlsx dilewati)')

    global SUBFOLDER
    # --- GRAFIK UTAMA BAB IV (batang berkelompok, sumbu-x payload, warna queue)
    SUBFOLDER = ''
    for q, akhir in [('Reliable', 'reliable'), ('Best Effort', 'besteffort')]:
        grafik_batang_kelompok(rs, q, 'Latency Rata-rata (ms)', 'Latensi rata-rata (ms)',
                               'Latensi rata-rata', f'grafik_latensi_{akhir}.png')
        grafik_batang_kelompok(rs, q, 'Packet Loss (%)', 'Packet loss (%)',
                               'Packet loss', f'grafik_packetloss_{akhir}.png',
                               desimal=3, akhiran='%')
        grafik_batang_kelompok(rs, q, 'Laju Kirim per Node (paket/s)', 'Pesan per detik',
                               'Laju kirim aktual per node', f'grafik_lajukirim_{akhir}.png',
                               garis_target=10)

    # --- Grafik garis (cara baca alternatif), disimpan terpisah
    SUBFOLDER = 'grafik_tambahan'
    for kolom, yl, nm, jd in [
            ('Latency Rata-rata (ms)', 'Latensi rata-rata (ms)', 'latensi', 'Latensi rata-rata'),
            ('Packet Loss (%)', 'Packet loss (%)', 'packetloss', 'Packet loss'),
            ('Laju Kirim per Node (paket/s)', 'Pesan per detik', 'laju_kirim',
             'Laju kirim aktual per node (target 10 pesan/detik)')]:
        grafik_faktor(rs, kolom, yl, f'{jd} terhadap ukuran payload',
                      f'garis_{nm}_vs_payload.png', 'Payload (Byte)')
        grafik_faktor(rs, kolom, yl, f'{jd} terhadap queue depth',
                      f'garis_{nm}_vs_queue.png', 'Queue')

    # --- Grafik pelengkap (sumbu-x = kode skenario), untuk lampiran
    SUBFOLDER = 'lampiran'
    grafik_batang(rs, 'Latency Rata-rata (ms)', 'Latensi (ms)',
                  'Latensi rata-rata seluruh skenario',
                  'lampiran_latensi_per_skenario.png')
    grafik_batang(rs, 'Packet Loss (%)', 'Packet loss (%)',
                  'Packet loss seluruh skenario',
                  'lampiran_packetloss_per_skenario.png')
    grafik_boxplot(bersih, rs, 'lampiran_boxplot_latensi.png')
    SUBFOLDER = ''
    grafik_waktu(bersih)

    # --- Tabel silang (baris queue, kolom payload) untuk Bab IV
    silang = {
        'Latensi rata-rata (ms)': tabel_silang(rs, 'Latency Rata-rata (ms)'),
        'Latensi median (ms)': tabel_silang(rs, 'Latency Median (ms)'),
        'Packet loss (%)': tabel_silang(rs, 'Packet Loss (%)'),
        'Laju kirim per node (pesan per detik)': tabel_silang(rs, 'Laju Kirim per Node (paket/s)'),
    }
    with open(os.path.join(OUT, 'tabel_silang.txt'), 'w') as f:
        for judul, t in silang.items():
            f.write(f'=== {judul} ===\n{t.to_string(na_rep="0 paket")}\n\n')
    try:
        with pd.ExcelWriter(os.path.join(OUT, 'ringkasan_hasil.xlsx'), mode='a',
                            engine='openpyxl') as xw:
            baris = 0
            for judul, t in silang.items():
                pd.DataFrame({judul: []}).to_excel(xw, sheet_name='Tabel Silang',
                                                    startrow=baris, index=False)
                t.to_excel(xw, sheet_name='Tabel Silang', startrow=baris + 1, na_rep='0 paket')
                baris += len(t) + 4
    except (ImportError, ValueError):
        pass

    pd.set_option('display.width', 220)
    print('\n' + rs[['No', 'Skenario', 'Paket Dikirim', 'Paket Diterima',
                     'Packet Loss (%)', 'Laju Kirim per Node (paket/s)',
                     'Latency Rata-rata (ms)', 'Latency Median (ms)',
                     'Latency P95 (ms)', 'Keterangan']]
          .to_string(index=False, na_rep='-'))
    print(f'\nSemua hasil tersimpan di: {OUT}')


if __name__ == '__main__':
    main()
