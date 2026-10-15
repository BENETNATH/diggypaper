import { redirect } from "next/navigation";

/** La question se définit maintenant sur la page « Analyser » (une seule étape). */
export default function QuestionRedirect({ params }: { params: { id: string } }) {
  redirect(`/projects/${params.id}/inference`);
}
