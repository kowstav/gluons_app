"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { api, Session } from "@/lib/api";
import { human } from "@/lib/format";
import Icon, { Brand } from "./icon";
import { ErrorBox, Success, Field } from "./ui";
import { ThemeButton } from "./theme";

const COPY: Record<string, { title: string; lead: string; submit: string }> = {
  "/login": { title: "Sign in", lead: "Open your business control room.", submit: "Sign in" },
  "/register": { title: "Create your account", lead: "Verify your email next, then add your business.", submit: "Create your account" },
  "/verify-email": { title: "Verify your email", lead: "Confirm your address before you connect a business.", submit: "Verify email" },
  "/forgot-password": { title: "Reset your password", lead: "Enter your email and we will send a reset link.", submit: "Send reset link" },
  "/reset-password": { title: "Choose a new password", lead: "Open the link from your email, then set a new password.", submit: "Update password" },
  "/mfa": { title: "Enter your verification code", lead: "Use the 6-digit code from your authenticator app, or a recovery code.", submit: "Verify and continue" },
};

function PasswordField({ fresh }: { fresh: boolean }) {
  const [visible, setVisible] = useState(false);
  return <div className="field"><label htmlFor="password">Password</label>
    <span className="input-with-action"><input id="password" name="password" type={visible ? "text" : "password"} required minLength={fresh ? 12 : undefined} maxLength={200} autoComplete={fresh ? "new-password" : "current-password"} spellCheck={false} aria-describedby={fresh ? "password-hint" : undefined} />
      <button type="button" aria-label={visible ? "Hide password" : "Show password"} aria-pressed={visible} onClick={() => setVisible(v => !v)}>{visible ? "Hide" : "Show"}</button></span>
    {fresh && <span className="field-hint" id="password-hint">At least 12 characters. A passphrase works well.</span>}</div>;
}

export function Auth({ path, initialError, onSignedIn }: { path: string; initialError: string | null; onSignedIn: () => Promise<void> }) {
  const router = useRouter(); const [error, setError] = useState(initialError); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState(""); const [token, setToken] = useState("");
  const page = COPY[path] ? path : "/login"; const copy = COPY[page];
  const register = page === "/register", verify = page === "/verify-email", reset = page === "/reset-password", forgot = page === "/forgot-password", mfa = page === "/mfa", login = page === "/login";
  useEffect(() => { setToken(new URLSearchParams(window.location.search).get("token") || ""); setNotice(""); setError(initialError); }, [path, initialError]);
  useEffect(() => { document.title = `${copy.title} · Gluons`; }, [copy.title]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null); setNotice("");
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try {
      if (register) { await api("/v1/auth/register", { method: "POST", body: fields }); setNotice("Check your email for a verification link. A local server writes messages to .gluons-data/mail instead of sending them."); }
      else if (verify) { await api("/v1/auth/verify-email", { method: "POST", body: { token } }); setNotice("Your email is verified. You can now sign in."); }
      else if (forgot) { await api("/v1/auth/password-reset/request", { method: "POST", body: fields }); setNotice("If that email is registered, a reset link is on its way."); }
      else if (reset) { await api("/v1/auth/password-reset/confirm", { method: "POST", body: { token, new_password: fields.password } }); setNotice("Password updated. Sign in with your new password."); }
      else if (mfa) { await api("/v1/auth/mfa/verify", { method: "POST", body: fields }); await onSignedIn(); }
      else { const result = await api<{ status: string }>("/v1/auth/login", { method: "POST", body: fields }); if (result.status === "mfa_required") router.push("/mfa"); await onSignedIn(); }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return <main className="auth-shell">
    <section className="auth-story" aria-label="How Gluons works"><Brand />
      <div><p className="auth-statement">Automation that waits for your signature.</p>
        <ul className="auth-principles">
          <li><strong>Connecting an account grants nothing</strong><span>A connected account cannot act until you sign a policy for it.</span></li>
          <li><strong>You approve the exact action</strong><span>The recipient, amount and record are shown before anything is sent.</span></li>
          <li><strong>Done means read back</strong><span>An action counts only after the provider’s own record confirms it.</span></li>
        </ul></div>
      <span className="auth-footnote"><Icon name="shield" size={16} /> The same rules apply from ChatGPT, Claude and Gluons Web.</span>
    </section>
    <section className="auth-form-wrap on-surface"><ThemeButton className="theme-corner" /><div className="auth-mobile-brand"><Brand /></div>
      <div className="auth-form"><h1>{copy.title}</h1><p>{copy.lead}</p>
        <ErrorBox error={error} /><Success>{notice}</Success>
        <form onSubmit={submit}>
          {register && <label>Your name<input name="display_name" required maxLength={200} autoComplete="name" /></label>}
          {(login || register || forgot) && <label>Email address<input name="email" type="email" required autoComplete="email" spellCheck={false} placeholder="you@yourbusiness.com" /></label>}
          {(login || register || reset) && <PasswordField fresh={register || reset} />}
          {verify && <Field label="Verification token" hint={<>Opening the link in your email fills this in.</>}><input value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" spellCheck={false} /></Field>}
          {reset && <Field label="Reset token" hint={<>Opening the link in your email fills this in.</>}><input value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" spellCheck={false} /></Field>}
          {mfa && <label>Verification code<input name="code" required autoComplete="one-time-code" inputMode="text" spellCheck={false} autoFocus /></label>}
          {login && <Link href="/forgot-password" className="forgot-link">Forgot password?</Link>}
          <button className="button primary wide" disabled={busy}>{busy ? "Please wait…" : copy.submit}</button>
        </form>
        {(login || register) && <p className="auth-switch">{register ? "Already have an account? " : "New to Gluons? "}<Link href={register ? "/login" : "/register"}>{register ? "Sign in" : "Create an account"}</Link></p>}
        {(verify || reset || forgot) && <Link className="text-button" href="/login">Back to sign in</Link>}
        {mfa && <button className="text-button" onClick={async () => { try { await api("/v1/auth/logout", { method: "POST" }); } catch { /* Already signed out. */ } router.push("/login"); await onSignedIn(); }}>Sign in with a different account</button>}
        <div className="auth-disclosure"><Icon name="shield" size={16} /><span>Your provider accounts stay yours. Connecting a service never grants permission to act.</span></div>
      </div>
    </section>
  </main>;
}

export function OrganizationSelect({ session, onDone }: { session: Session; onDone: () => Promise<void> }) {
  const router = useRouter(); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const hasBusiness = session.organizations.length > 0;
  useEffect(() => { document.title = "Choose a business · Gluons"; }, []);
  async function choose(id: string) { setBusy(true); setError(null); try { await api(`/v1/organizations/${id}/select`, { method: "POST" }); await onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null);
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try { await api("/v1/organizations", { method: "POST", body: { name: fields.name, industry: fields.industry || null, country: String(fields.country || "").toUpperCase() || null } }); await onDone(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <main className="onboarding-page on-surface"><Brand />
    <div className="onboarding-card"><h1>{hasBusiness ? "Choose a business" : "Add your first business"}</h1>
      <p>Each business keeps its own connections, people, permissions and budget. Signed in as {session.user.email}.</p>
      <ErrorBox error={error} />
      {session.organizations.map(o => <button className="org-choice" key={o.id} disabled={busy} onClick={() => choose(o.id)}><span className="workspace-avatar">{o.name[0]}</span><span><strong>{o.name}</strong><small>{human(o.role)}{o.id === session.active_organization_id ? " · current" : ""}</small></span><Icon name="chevron" size={18} /></button>)}
      {hasBusiness && <div className="form-divider">or add another business</div>}
      <form onSubmit={create}>
        <label>Business name<input name="name" required maxLength={200} autoComplete="organization" /></label>
        <div className="form-grid"><label>Industry<input name="industry" placeholder="Food distribution" autoComplete="off" /></label><label>Country code<input name="country" minLength={2} maxLength={2} placeholder="IN" pattern="[A-Za-z]{2}" title="Two-letter country code, such as IN or US" autoComplete="off" spellCheck={false} /></label></div>
        <button className="button primary wide" disabled={busy}>Create business workspace</button>
      </form>
      <div className="button-row" style={{ marginTop: 16, justifyContent: "space-between" }}>
        {session.active_organization_id ? <Link className="text-button" href="/">Back to {session.organizations.find(o => o.id === session.active_organization_id)?.name || "workspace"}</Link> : <span />}
        <button className="text-button" onClick={async () => { try { await api("/v1/auth/logout", { method: "POST" }); } catch { /* Already signed out. */ } router.push("/login"); await onDone(); }}>Sign out</button>
      </div>
    </div></main>;
}
