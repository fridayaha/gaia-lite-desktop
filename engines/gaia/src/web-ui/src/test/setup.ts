/**
 * Vitest global setup (P1, ADR-011).
 *
 * Registers @testing-library/jest-dom matchers so component tests can use
 * assertions like `expect(el).toBeInTheDocument()`.
 */
import '@testing-library/jest-dom/vitest';
