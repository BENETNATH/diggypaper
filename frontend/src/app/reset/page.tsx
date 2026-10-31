"use client";

import { useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ApiError, confirmPasswordReset } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Card } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";

export default function ResetPage() {
  const t = useTranslations("auth");
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t("passwordMismatch"));
      return;
    }
    setLoading(true);
    try {
      await confirmPasswordReset(token, password);
      setSuccess(true);
    } catch (err) {
      if (err instanceof ApiError) {
        // invalid_token / expired_token (and any failure): one clear error.
        setError(t("invalidResetLink"));
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
        <h1 className="mb-4 text-xl font-bold text-foreground">{t("resetTitle")}</h1>
        {success ? (
          <>
            <p className="text-sm text-success">{t("resetSuccess")}</p>
            <Link href="/login" className="mt-4 inline-block text-sm text-primary hover:underline">
              {t("loginButton")}
            </Link>
          </>
        ) : (
          <>
            {error && (
              <div className="mb-4">
                <ErrorBanner message={error} />
                {error === t("invalidResetLink") && (
                  <Link href="/login" className="mt-2 inline-block text-sm text-primary hover:underline">
                    {t("newResetLink")}
                  </Link>
                )}
              </div>
            )}
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <Field
                label={t("password")}
                error={password.length > 0 && password.length < 8 ? t("passwordMin") : null}
              >
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </Field>
              <Field
                label={t("confirmPassword")}
                error={confirm.length > 0 && confirm !== password ? t("passwordMismatch") : null}
              >
                <Input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </Field>
              <Button
                type="submit"
                loading={loading}
                disabled={!token || password.length < 8 || password !== confirm}
              >
                {t("resetButton")}
              </Button>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}
