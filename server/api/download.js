// Gemlock indirme adresi: GitHub'daki en güncel kurulum dosyasını tarayıcıya "indir" olarak verir.
const SRC = "https://raw.githubusercontent.com/YalinFrncgllr/Gemlock/main/Gemlock%20Setup.pyw";

module.exports = async (req, res) => {
  try {
    const r = await fetch(SRC, { headers: { "User-Agent": "Gemlock-Server" } });
    if (!r.ok) throw new Error(String(r.status));
    const body = Buffer.from(await r.arrayBuffer());
    res.statusCode = 200;
    res.setHeader("Content-Type", "application/octet-stream");
    res.setHeader("Content-Disposition", 'attachment; filename="Gemlock Setup.pyw"');
    res.setHeader("Cache-Control", "public, s-maxage=300, stale-while-revalidate=3600");
    res.end(body);
  } catch (e) {
    res.statusCode = 302;
    res.setHeader("Location", "https://github.com/YalinFrncgllr/Gemlock");
    res.end();
  }
};
