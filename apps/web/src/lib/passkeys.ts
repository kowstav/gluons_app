import { api } from "./api";

type CreationOptions = Parameters<typeof PublicKeyCredential.parseCreationOptionsFromJSON>[0];
type RequestOptions = Parameters<typeof PublicKeyCredential.parseRequestOptionsFromJSON>[0];

function requirePasskeys() {
  if (typeof PublicKeyCredential === "undefined" ||
      typeof PublicKeyCredential.parseCreationOptionsFromJSON !== "function" ||
      typeof PublicKeyCredential.parseRequestOptionsFromJSON !== "function") {
    throw new Error("Open Gluons in a browser that supports passkeys.");
  }
}

export async function enrollPasskey() {
  requirePasskeys();
  const challenge = await api<{ challenge_id: string; options: CreationOptions }>(
    "/v1/auth/passkey/enrollment/options", { method: "POST" },
  );
  const credential = await navigator.credentials.create({
    publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(challenge.options),
  });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Passkey enrollment was cancelled.");
  return api("/v1/auth/passkey/enrollment/finish", {
    method: "POST", body: { challenge_id: challenge.challenge_id, credential: credential.toJSON() },
  });
}

export async function signPolicyWithPasskey(path: string) {
  requirePasskeys();
  const challenge = await api<{ challenge_id: string; options: RequestOptions }>(
    `${path}/passkey-options`, { method: "POST" },
  );
  const credential = await navigator.credentials.get({
    publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(challenge.options),
  });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Passkey verification was cancelled.");
  return api(`${path}/sign`, {
    method: "POST", body: { challenge_id: challenge.challenge_id, credential: credential.toJSON() },
  });
}
