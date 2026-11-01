"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { ApiError, getPublicConfig, requestPasswordReset } from "@/lib/api";
import { readLocaleCookie, writeLocaleCookie } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/Button";
import { Field, Input, Checkbox } from "@/components/ui/Field";
import { Card } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";

export default function LoginPage() {
  const t = useTranslations("auth");
  const { login } = useAuth();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);
  const [consentRequired, setConsentRequired] = useState(false);
  const [privacyUrl, setPrivacyUrl] = useState<string | null>(null);
  const [configLoaded, setConfigLoaded] = useState(false);

  useEffect(() => {
    getPublicConfig()
      .then((c) => setPrivacyUrl(c.privacy_url))
      .catch(() => undefined)
      .finally(() => setConfigLoaded(true));
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotSent, setForgotSent] = useState(false);
  const [forgotError, setForgotError] = useState<string | null>(null);
  const [forgotLoading, setForgotLoading] = useState(false);

  async function handleForgot(e: React.FormEvent) {
    e.preventDefault();
    setForgotError(null);
    setForgotLoading(true);
    try {
      await requestPasswordReset(forgotEmail);
      setForgotOpen(false);
      setForgotSent(true); // generic message: never reveals whether the account exists
    } catch (err) {
      setForgotError(err instanceof Error ? err.message : t("unknownError"));
    } finally {
      setForgotLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const u = await login(email, password, consent || undefined);
      // New device (no remembered language choice): we take the account's language.
      if (!readLocaleCookie() && u.language_preference) {
        writeLocaleCookie(u.language_preference);
        router.push("/projects");
        return;
      }
      router.push("/projects");
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setConsentRequired(true);
        setError(t("consentRequired"));
      } else {
        setError(err instanceof Error ? err.message : t("unknownError"));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-md">
        <h1 className="mb-4 text-xl font-bold text-foreground">{t("loginTitle")}</h1>
        {error && (
          <div className="mb-4">
            <ErrorBanner message={error} />
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Field label={t("email")}>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </Field>
          <Field label={t("password")}>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </Field>
          {/* GDPR consent: asked only once (the server remembers it). */}
          {consentRequired && (
            <>
              {/* no privacy URL: do not cite a policy the user cannot read */}
              <Checkbox
                label={configLoaded && !privacyUrl ? t("consentLabelNoPolicy") : t("consentLabel")}
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <p className="-mt-2 text-xs text-muted">{t("consentFirstLogin")}</p>
              {privacyUrl && (
                <a href={privacyUrl} target="_blank" rel="noopener noreferrer" className="-mt-2 text-xs text-primary hover:underline">
                  {t("privacyPolicy")}
                </a>
              )}
            </>
          )}
          <Button type="submit" loading={loading}>
            {t("loginButton")}
          </Button>
          <Link href="/register" className="text-center text-sm text-primary hover:underline">
            {t("registerWithCode")}
          </Link>
          {/* Forgot password: inline form, generic result (never reveals existence). */}
          {forgotSent && <p className="text-sm text-success">{t("forgotSent")}</p>}
          {forgotError && (
            <div>
              <ErrorBanner message={forgotError} />
            </div>
          )}
          {forgotOpen ? (
            <form onSubmit={handleForgot} className="flex flex-col gap-2">
              <Field label={t("email")}>
                <Input
                  type="email"
                  value={forgotEmail}
                  onChange={(e) => setForgotEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </Field>
              <Button type="submit" loading={forgotLoading}>
                {t("forgotSend")}
              </Button>
            </form>
          ) : (
            <button
              type="button"
              className="text-center text-sm text-primary hover:underline"
              onClick={() => {
                setForgotOpen(true);
                setForgotSent(false);
                setForgotError(null);
              }}
            >
              {t("forgotPassword")}
            </button>
          )}
        </form>
      </Card>
    </div>
  );
}
