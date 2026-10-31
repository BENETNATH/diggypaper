"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { readLocaleCookie, writeLocaleCookie } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Card } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";

export default function RegisterPage() {
  const t = useTranslations("auth");
  const { register } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlToken = searchParams.get("token") ?? "";

  const [tokenInput, setTokenInput] = useState(urlToken);
  // The token field is hidden when the link carries a token — except when that
  // token is invalid/expired (401): the user can then paste another one.
  const [showTokenField, setShowTokenField] = useState(!urlToken);

  // The registration page opens in the language chosen by the inviter
  // (the link's `language` claim), unless the invitee already chose their own language.
  useEffect(() => {
    try {
      const claims = JSON.parse(atob(urlToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      if (["fr", "en", "de", "es"].includes(claims.language) && !readLocaleCookie()) {
        writeLocaleCookie(claims.language);
        window.location.reload();
      }
    } catch {
      /* link missing or unreadable: default language */
    }
  }, [urlToken]);
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await register(tokenInput, password, fullName || undefined);
      router.push("/login");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("invalidToken"));
        setShowTokenField(true);
      } else if (err instanceof ApiError && err.status === 409) {
        setError(t("userExists"));
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
        <h1 className="mb-4 text-xl font-bold text-foreground">{t("registerTitle")}</h1>
        {error && (
          <div className="mb-4">
            <ErrorBanner message={error} />
            {error === t("invalidToken") && (
              <p className="mt-2 text-sm text-muted">{t("invalidTokenHint")}</p>
            )}
            {error === t("userExists") && (
              <Link href="/login" className="mt-2 inline-block text-sm text-primary hover:underline">
                {t("loginButton")}
              </Link>
            )}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {showTokenField && (
            <Field label={t("token")}>
              <Input
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder={t("tokenPlaceholder")}
                required
              />
            </Field>
          )}
          <Field label={t("fullName")}>
            <Input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
            />
          </Field>
          <Field label={t("password")} error={password.length > 0 && password.length < 8 ? t("passwordMin") : null}>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </Field>
          <Button type="submit" loading={loading} disabled={password.length < 8 || !tokenInput.trim()}>
            {t("registerButton")}
          </Button>
        </form>
      </Card>
    </div>
  );
}
