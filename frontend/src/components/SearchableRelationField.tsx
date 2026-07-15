import { useEffect, useId, useState } from 'react';

interface SearchableRelationFieldProps {
  value?: string;
  suggested?: string;
  options: string[];
  placeholder: string;
  specialLabel: string;
  disabled?: boolean;
  ariaLabel?: string;
  onChange: (value: string) => void;
}

function normalizeSearch(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export function SearchableRelationField({
  value,
  suggested = '',
  options,
  placeholder,
  specialLabel,
  disabled = false,
  ariaLabel,
  onChange,
}: SearchableRelationFieldProps) {
  const menuId = useId();
  const displayValue = value !== undefined ? (value === '' ? specialLabel : value) : suggested;
  const [query, setQuery] = useState(displayValue);
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!open) setQuery(displayValue);
  }, [displayValue, open]);

  const search = showAll || query === specialLabel ? '' : query.trim();
  const normalizedSearch = normalizeSearch(search);
  const filtered = options
    .filter((option) => !normalizedSearch || normalizeSearch(option).includes(normalizedSearch))
    .slice(0, 80);
  const exact = options.some((option) => normalizeSearch(option) === normalizedSearch);
  const canAdd = Boolean(search && !exact && normalizeSearch(suggested) !== normalizedSearch);

  function choose(nextValue: string): void {
    onChange(nextValue);
    setQuery(nextValue === '' ? specialLabel : nextValue);
    setOpen(false);
    setShowAll(false);
  }

  function handleChange(nextQuery: string): void {
    setQuery(nextQuery);
    setShowAll(false);
    onChange(nextQuery === specialLabel ? '' : nextQuery);
    setOpen(true);
  }

  return (
    <div className="combo">
      <div className="combo-control">
        <input
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-controls={menuId}
          aria-expanded={open && !disabled}
          role="combobox"
          value={disabled ? '' : query}
          disabled={disabled}
          placeholder={disabled ? specialLabel : placeholder}
          onFocus={(event) => {
            event.currentTarget.select();
            setOpen(true);
            setShowAll(true);
          }}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onChange={(event) => handleChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setOpen(false);
          }}
        />
      </div>
      {open && !disabled && (
        <div className="combo-menu" id={menuId} role="listbox">
          <span className="combo-count">
            {options.length} option{options.length > 1 ? 's' : ''} disponible{options.length > 1 ? 's' : ''}
          </span>
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => choose('')}>
            {specialLabel}
          </button>
          {suggested && normalizeSearch(suggested) !== normalizeSearch(search) && (
            <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => choose(suggested)}>
              Proposition : {suggested}
            </button>
          )}
          {filtered.map((option) => (
            <button key={option} type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => choose(option)}>
              {option}
            </button>
          ))}
          {canAdd && (
            <button type="button" className="combo-add" onMouseDown={(event) => event.preventDefault()} onClick={() => choose(search)}>
              Ajouter « {search} »
            </button>
          )}
          {filtered.length === 0 && !canAdd && <span>Aucune option ne correspond à la recherche</span>}
        </div>
      )}
    </div>
  );
}
