"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { ApiError, getPublicConfig } from "@/lib/api";
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

  useEffect(() => {
    getPublicConfig()
      .then((c) => setPrivacyUrl(c.privacy_url))
      .catch(() => undefined);
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const u = await login(email, password, consent || undefined);
      // Nouvel appareil (pas de choix de langue mémorisé) : on prend la langue du compte.
      if (!readLocaleCookie() && u.language_preference) {
        writeLocaleCookie(u.language_preference);
        window.location.assign("/projects");
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
          {/* Consentement RGPD : demandé une seule fois (le serveur le mémorise). */}
          {consentRequired && (
            <>
              <Checkbox
                label={t("consentLabel")}
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
        </form>
      </Card>
    </div>
  );
}
