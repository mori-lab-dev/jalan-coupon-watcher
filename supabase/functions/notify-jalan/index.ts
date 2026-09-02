// supabase/functions/notify-jalan/index.ts
//
// じゃらんクーポン監視の通知メール送信。
// jalan_watcher (Python/GitHub Actions) が組み立てた subject/text/html を受け取り、
// Gmail SMTP で送信する。認証情報(GMAIL_USER/GMAIL_APP_PASSWORD/MAIL_TO)は
// このEdge Function側のシークレットにのみ置き、呼び出し元(GitHub Actions)は持たない。
//
// 経緯: GitHub ActionsからGmail SMTPを直接叩く方式は、GMAIL_USERが誤って
// 別アカウント(個人アドレス)のまま設定されており、アプリパスワードの発行先
// (morilab.support@gmail.com)と一致せず常に535 Bad Credentialsで失敗していた
// (2026-09-02)。認証情報をサーバ側1箇所に一本化し、呼び出し元の設定ミスの
// 影響を無くす。
//
// 送信失敗時も HTTP 200 で {ok:true, mailed:false} を返す（呼び出し元が
// mailed を見て判断する）。呼び出し元(main.py)は mailed=false を失敗として
// 扱い、DB保存を見送って次回再検知させる想定。

const GMAIL_USER = Deno.env.get("GMAIL_USER") ?? "";
const GMAIL_PASS = Deno.env.get("GMAIL_APP_PASSWORD") ?? "";
const MAIL_TO = (Deno.env.get("MAIL_TO") ?? "")
  .split(",").map((s) => s.trim()).filter(Boolean);

function toB64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

// RFC2047 base64 encoded-word。各ワードは文字境界で分割し、折返しつつ75文字以内に収める。
function encodeSubject(s: string): string {
  if (/^[\x20-\x7E]*$/.test(s)) return s;
  const enc = new TextEncoder();
  const words: string[] = [];
  let cur = "";
  for (const ch of Array.from(s)) {
    if (enc.encode(cur + ch).length > 45) { if (cur) words.push(cur); cur = ch; }
    else cur += ch;
  }
  if (cur) words.push(cur);
  return words.map((w) => `=?UTF-8?B?${toB64(enc.encode(w))}?=`).join("\r\n ");
}

function encodeBodyBase64(text: string): string {
  const b64 = toB64(new TextEncoder().encode(text));
  return b64.replace(/(.{76})/g, "$1\r\n");
}

async function sendMail(opts: { subject: string; text: string; html?: string }):
    Promise<{ ok: boolean; error?: string }> {
  if (!GMAIL_USER || !GMAIL_PASS || !MAIL_TO.length) {
    return { ok: false, error: "GMAIL_USER/GMAIL_APP_PASSWORD/MAIL_TO が未設定" };
  }
  const conn = await Deno.connectTls({ hostname: "smtp.gmail.com", port: 465 });
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const buf = new Uint8Array(8192);
  const read = async (): Promise<string> => {
    const n = await conn.read(buf);
    return n ? dec.decode(buf.subarray(0, n)) : "";
  };
  const cmd = async (line: string | null, expect: string) => {
    if (line !== null) await conn.write(enc.encode(line + "\r\n"));
    const res = await read();
    if (!res.startsWith(expect)) throw new Error(`SMTP ${expect} 期待, 実際: ${res.trim().slice(0, 160)}`);
  };
  try {
    await cmd(null, "220");
    await cmd("EHLO jalan-coupon-watcher", "250");
    await cmd("AUTH LOGIN", "334");
    await cmd(toB64(enc.encode(GMAIL_USER)), "334");
    await cmd(toB64(enc.encode(GMAIL_PASS)), "235");
    await cmd(`MAIL FROM:<${GMAIL_USER}>`, "250");
    for (const to of MAIL_TO) {
      await cmd(`RCPT TO:<${to}>`, "250");
    }
    await cmd("DATA", "354");

    const boundary = `----jalan-${crypto.randomUUID()}`;
    let msg =
      `From: MORI-LAB じゃらん監視 <${GMAIL_USER}>\r\n` +
      `To: ${MAIL_TO.join(", ")}\r\n` +
      `Subject: ${encodeSubject(opts.subject)}\r\n` +
      `Date: ${new Date().toUTCString()}\r\n` +
      `MIME-Version: 1.0\r\n`;

    if (opts.html) {
      msg += `Content-Type: multipart/alternative; boundary="${boundary}"\r\n\r\n`;
      msg += `--${boundary}\r\n` +
        `Content-Type: text/plain; charset=UTF-8\r\n` +
        `Content-Transfer-Encoding: base64\r\n\r\n` +
        encodeBodyBase64(opts.text) + `\r\n\r\n`;
      msg += `--${boundary}\r\n` +
        `Content-Type: text/html; charset=UTF-8\r\n` +
        `Content-Transfer-Encoding: base64\r\n\r\n` +
        encodeBodyBase64(opts.html) + `\r\n\r\n`;
      msg += `--${boundary}--\r\n`;
    } else {
      msg += `Content-Type: text/plain; charset=UTF-8\r\n` +
        `Content-Transfer-Encoding: base64\r\n\r\n` +
        encodeBodyBase64(opts.text);
    }
    msg += `\r\n.\r\n`;

    await conn.write(enc.encode(msg));
    await cmd(null, "250");
    try { await conn.write(enc.encode("QUIT\r\n")); } catch (_) { /* noop */ }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  } finally {
    try { conn.close(); } catch (_) { /* noop */ }
  }
}

Deno.serve(async (req) => {
  let payload: { subject?: string; text?: string; html?: string };
  try {
    payload = await req.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "invalid json" }),
      { status: 400, headers: { "Content-Type": "application/json" } });
  }
  const subject = (payload.subject ?? "").trim();
  const text = payload.text ?? "";
  if (!subject || !text) {
    return new Response(JSON.stringify({ ok: false, error: "subject/text is required" }),
      { status: 400, headers: { "Content-Type": "application/json" } });
  }

  const r = await sendMail({ subject, text, html: payload.html });
  if (!r.ok) console.error("[notify-jalan] 送信失敗:", r.error);

  return new Response(JSON.stringify({ ok: true, mailed: r.ok, error: r.error }),
    { headers: { "Content-Type": "application/json" } });
});
