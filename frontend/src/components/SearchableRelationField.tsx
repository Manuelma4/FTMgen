import { useEffect, useId, useMemo, useState } from 'react';

interface SearchableRelationOption {
  value: string;
  label: string;
}

interface SearchableRelationFieldProps {
  value?: string;
  suggested?: string;
  options: Array<string | SearchableRelationOption>;
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
  const normalizedOptions = useMemo(() => {
    const byValue = new Map<string, SearchableRelationOption>();
    for (const option of options) {
      const normalized = typeof option === 'string' ? { value: option, label: option } : option;
      if (normalized.value && !byValue.has(normalized.value)) byValue.set(normalized.value, normalized);
    }
    return Array.from(byValue.values());
  }, [options]);
  const suggestedOption = normalizedOptions.find((option) => (
    option.value === suggested || normalizeSearch(option.label) === normalizeSearch(suggested)
  ));
  const selectedOption = value === undefined
    ? suggestedOption
    : normalizedOptions.find((option) => (
      option.value === value || normalizeSearch(option.label) === normalizeSearch(value)
    ));
  const displayValue = value === '' ? specialLabel : (selectedOption?.label || (value ?? ''));
  const [query, setQuery] = useState(displayValue);
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  useEffect(() => {
    if (!open) setQuery(displayValue);
  }, [displayValue, open]);

  const search = showAll || query === specialLabel ? '' : query.trim();
  const normalizedQuery = normalizeSearch(search);
  const filtered = normalizedOptions
    .filter((option) => !normalizedQuery || normalizeSearch(option.label).includes(normalizedQuery))
    .slice(0, 80);
  const choices = [
    { value: '', label: specialLabel, kind: 'special' as const },
    ...(suggestedOption ? [{ ...suggestedOption, kind: 'suggested' as const }] : []),
    ...filtered
      .filter((option) => option.value !== suggestedOption?.value)
      .map((option) => ({ ...option, kind: 'option' as const })),
  ];

  function findExactOption(nextQuery: string): SearchableRelationOption | undefined {
    const normalized = normalizeSearch(nextQuery);
    return normalizedOptions.find((option) => (
      normalizeSearch(option.label) === normalized || normalizeSearch(option.value) === normalized
    ));
  }

  function choose(nextValue: string): void {
    const option = normalizedOptions.find((item) => item.value === nextValue);
    onChange(nextValue);
    setQuery(nextValue === '' ? specialLabel : (option?.label || nextValue));
    setOpen(false);
    setShowAll(false);
    setActiveIndex(-1);
  }

  function resetQuery(): void {
    setQuery(displayValue);
    setOpen(false);
    setShowAll(false);
    setActiveIndex(-1);
  }

  function commitExactQuery(): void {
    if (normalizeSearch(query) === normalizeSearch(specialLabel)) {
      choose('');
      return;
    }
    const exact = findExactOption(query);
    if (exact) choose(exact.value);
    else resetQuery();
  }

  return (
    <div className="combo">
      <div className="combo-control">
        <input
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-controls={menuId}
          aria-expanded={open && !disabled}
          aria-haspopup="listbox"
          aria-activedescendant={open && activeIndex >= 0 ? `${menuId}-option-${activeIndex}` : undefined}
          role="combobox"
          value={disabled ? '' : query}
          disabled={disabled}
          placeholder={disabled ? specialLabel : placeholder}
          onFocus={(event) => {
            event.currentTarget.select();
            setOpen(true);
            setShowAll(true);
            setActiveIndex(-1);
          }}
          onBlur={commitExactQuery}
          onChange={(event) => {
            setQuery(event.target.value);
            setShowAll(false);
            setOpen(true);
            setActiveIndex(-1);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setOpen(true);
              setActiveIndex((current) => Math.min(current + 1, choices.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setOpen(true);
              setActiveIndex((current) => Math.max(current - 1, 0));
            } else if (event.key === 'Enter') {
              event.preventDefault();
              if (open && activeIndex >= 0 && choices[activeIndex]) choose(choices[activeIndex].value);
              else commitExactQuery();
            } else if (event.key === 'Escape') {
              resetQuery();
            }
          }}
        />
      </div>
      {open && !disabled && (
        <div className="combo-menu" id={menuId} role="listbox">
          <span className="combo-count">
            {normalizedOptions.length} option{normalizedOptions.length > 1 ? 's' : ''} disponible{normalizedOptions.length > 1 ? 's' : ''}
          </span>
          {choices.map((option, index) => (
            <button
              id={`${menuId}-option-${index}`}
              key={`${option.kind}-${option.value}`}
              type="button"
              role="option"
              aria-selected={activeIndex === index}
              tabIndex={-1}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option.value)}
            >
              {option.kind === 'suggested' ? `Proposition : ${option.label}` : option.label}
            </button>
          ))}
          {filtered.length === 0 && <span>Aucune option ne correspond à la recherche</span>}
        </div>
      )}
    </div>
  );
}
