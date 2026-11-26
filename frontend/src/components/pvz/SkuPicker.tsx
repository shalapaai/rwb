/**
 * Поле артикула для приёма отказа с поиском по каталогу.
 * Фокус при выборе клавиатурой остаётся в поле, а список прокручивается к товару.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { Stock } from "./pvz-api";

type Props = {
  value: string;
  products: Stock["products"];
  disabled: boolean;
  onChange: (sku: string) => void;
};

export default function SkuPicker({ value, products, disabled, onChange }: Props) {
  const id = useId();
  const listId = `${id}-options`;
  const menu = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(-1);
  const query = value.trim().toLowerCase();
  const options = products.filter(({ sku, name }) =>
    sku.toLowerCase().includes(query) || name.toLowerCase().includes(query),
  );
  const visible = open && !disabled && options.length > 0;
  const active = visible ? options[index] : undefined;

  useEffect(() => {
    if (active) menu.current?.children[index]?.scrollIntoView({ block: "nearest" });
  }, [active, index]);

  function close() {
    setOpen(false);
    setIndex(-1);
  }

  function select(sku: string) {
    onChange(sku);
    close();
  }

  function navigate(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      close();
    } else if (event.key === "Enter" && active) {
      event.preventDefault();
      select(active.sku);
    } else if (options.length && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      setOpen(true);
      setIndex(event.key === "ArrowDown" ? Math.min(index + 1, options.length - 1)
        : index < 0 ? options.length - 1 : Math.max(0, index - 1));
    }
  }

  return (
    <div className="pvz-sku" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) close();
    }}>
      <label htmlFor={id}>Артикул товара</label>
      <div className="pvz-sku-field">
        <input id={id} value={value} disabled={disabled} autoComplete="off" role="combobox"
          aria-autocomplete="list" aria-expanded={visible} aria-controls={visible ? listId : undefined}
          aria-activedescendant={active ? `${listId}-${index}` : undefined}
          onFocus={() => setOpen(true)} onClick={() => setOpen(true)} onKeyDown={navigate}
          onChange={(event) => {
            onChange(event.target.value);
            setIndex(-1);
            setOpen(true);
            if (menu.current) menu.current.scrollTop = 0;
          }} />
      </div>
      {visible && (
        <ul ref={menu} id={listId} role="listbox" aria-label="Артикулы товаров"
          tabIndex={-1} className="pvz-sku-options">
          {options.map(({ sku, name, volume_l }, i) => (
            <li key={sku} id={`${listId}-${i}`} role="option" aria-selected={i === index}
              onMouseDown={(event) => event.preventDefault()} onClick={() => select(sku)}>
              <strong>{sku}</strong>
              <span>{name && name !== sku && `${name} · `}{volume_l.toLocaleString("ru-RU")} л</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
