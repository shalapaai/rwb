/** Заглушка экрана, пока задача роадмапа не сделана. */
export default function Placeholder(props: { title: string; tasks: string; what: string }) {
  return (
    <section>
      <h1>{props.title}</h1>
      <p>{props.what}</p>
      <p className="muted">Экран ещё не сделан — задачи роадмапа {props.tasks}.</p>
    </section>
  );
}
