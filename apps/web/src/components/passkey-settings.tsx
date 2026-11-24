"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { enrollPasskey } from "@/lib/passkeys";

export default function PasskeySettings() {
  const [status, setStatus] = useState<{ available: boolean; enrolled: boolean } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api<{ available: boolean; enrolled: boolean }>("/v1/auth/passkey")
      .then(setStatus, (error: Error) => setFailure(error.message));
  }, []);

  async function addPasskey() {
    setBusy(true);
    setFailure(null);
    try {
      await enrollPasskey();
      setStatus({ available: true, enrolled: true });
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <div>
    {failure && <p className="error-box" role="alert">{failure}</p>}
    {status?.enrolled ? <p className="small-print" role="status">Passkey enrolled for spending approvals.</p>
      : status?.available && <button className="button secondary" disabled={busy} onClick={addPasskey}>Add a passkey</button>}
  </div>;
}
