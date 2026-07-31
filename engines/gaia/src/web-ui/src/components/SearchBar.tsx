import type { ChangeEvent } from 'react';
import { cn } from '../lib/cn';

export interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function SearchBar({ value, onChange, placeholder = '搜索…', className }: SearchBarProps) {
  return (
    <div className={cn('search-wrap', className)}>
      <input
        className="search-input"
        type="text"
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onChange('');
        }}
        placeholder={placeholder}
      />
    </div>
  );
}
