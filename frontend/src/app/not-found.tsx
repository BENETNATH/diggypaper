import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { EmptyState } from "@/components/ui/Feedback";

export default async function NotFound() {
  const t = await getTranslations("common");
  const tn = await getTranslations("nav");
  return (
    <EmptyState
      title={t("pageNotFound")}
      action={
        <Link href="/projects" className="text-sm text-primary hover:underline">
          {tn("projects")}
        </Link>
      }
    />
  );
}
