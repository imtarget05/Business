type Props = {
  title: string;
  description: string;
};

export default function PagePlaceholder({ title, description }: Props) {
  return (
    <section>
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="mt-1 max-w-2xl text-sm text-slate-600">{description}</p>
      <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400">
        Data will appear here once the backend API is connected (Phase 1).
      </div>
    </section>
  );
}
