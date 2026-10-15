"use client";

import { useEffect, useState } from "react";
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

  // La page d'inscription s'ouvre dans la langue choisie par la personne qui invite
  // (claim `language` du lien), sauf si l'invité a déjà choisi sa propre langue.
  useEffect(() => {
    try {
      const claims = JSON.parse(atob(urlToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      if (["fr", "en", "de", "es"].includes(claims.language) && !readLocaleCookie()) {
        writeLocaleCookie(claims.language);
        window.location.reload();
      }
    } catch {
      /* lien absent ou illisible : langue par défaut */
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
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {!urlToken && (
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
