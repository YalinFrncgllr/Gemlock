// Sunucu durumu. ?check=1 ile anahtarın Henrik tarafından kabul edildiğini de kontrol eder
// (anahtarın kendisini asla göstermez).
module.exports = async (req, res) => {
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  const key = process.env.HENRIK_KEY;
  const out = { ok: true, configured: Boolean(key) };
  if (key && new URL(req.url, "http://x").searchParams.get("check") === "1") {
    try {
      const r = await fetch("https://api.henrikdev.xyz/valorant/v1/status/eu", {
        headers: { Authorization: key, "User-Agent": "Gemlock-Server" },
      });
      out.key_status = r.status; // 200 = anahtar geçerli, 401/403 = geçersiz
    } catch (e) {
      out.key_status = "unreachable";
    }
  }
  res.end(JSON.stringify(out));
};
