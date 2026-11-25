import { useEffect, useState } from "react";
import { api, type Role } from "./api";

const ROLES: { id: Role; title: string }[] = [
  { id: "pvz_operator", title: "Оператор ПВЗ" },
  { id: "seller", title: "Продавец" },
  { id: "logist", title: "Логист" },
  { id: "admin", title: "Сценарии" },
];

type Health = { status: string; db: string };

export default function App() {
  const [role, setRole] = useState<Role>("pvz_operator");
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Health>("/health", role)
      .then((h) => {
        setHealth(h);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [role]);

  return (
    <div className="layout">
      <header>
        <strong>Mesh ПВЗ</strong>
        <nav>
          {ROLES.map((r) => (
            <button key={r.id} className={r.id === role ? "active" : ""} onClick={() => setRole(r.id)}>
              {r.title}
            </button>
          ))}
        </nav>
        <span className="status">
          API: {error ? "недоступен" : health ? `${health.status}, БД ${health.db}` : "…"}
        </span>
      </header>
      <main>
        <h1>{ROLES.find((r) => r.id === role)?.title}</h1>
        <p className="muted">Экран роли появится на хакатоне.</p>
      </main>
    </div>
  );
}
