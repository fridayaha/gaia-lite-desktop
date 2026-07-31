/**
 * Tests for the LoginPage (ADR-016/017 Phase 5).
 *
 * Verifies the form renders email/password fields, toggles between sign-in
 * and sign-up modes, calls the auth hook's login/signUp, and surfaces
 * errors. The useAuth hook is mocked so the test is isolated from Better
 * Auth SDK behavior.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const loginMock = vi.fn();
const signUpMock = vi.fn();
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    authEnabled: true,
    jwt: null,
    login: loginMock,
    signUp: signUpMock,
    logout: vi.fn(),
  }),
}));

import { LoginPage } from '../LoginPage';

beforeEach(() => {
  loginMock.mockReset();
  signUpMock.mockReset();
});

describe('LoginPage', () => {
  it('renders email + password fields and a submit button', () => {
    render(<LoginPage />);
    expect(screen.getByLabelText('邮箱')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('toggles to sign-up mode and shows the name field', () => {
    render(<LoginPage />);
    fireEvent.click(screen.getByText('注册'));
    expect(screen.getByLabelText('姓名')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '注册并登录' })).toBeInTheDocument();
  });

  it('calls login on submit with email + password', async () => {
    loginMock.mockResolvedValue(undefined);
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password1' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith('a@b.com', 'password1');
    });
  });

  it('calls signUp on submit in sign-up mode', async () => {
    signUpMock.mockResolvedValue(undefined);
    render(<LoginPage />);
    fireEvent.click(screen.getByText('注册'));
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: 'Alice' } });
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password1' } });
    fireEvent.click(screen.getByRole('button', { name: '注册并登录' }));
    await waitFor(() => {
      expect(signUpMock).toHaveBeenCalledWith('a@b.com', 'password1', 'Alice');
    });
  });

  it('surfaces an error when login throws', async () => {
    loginMock.mockRejectedValue(new Error('密码错误'));
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password1' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('密码错误');
    });
  });

  it('disables the submit button until email + password are filled', () => {
    render(<LoginPage />);
    const btn = screen.getByRole('button', { name: '登录' });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'a@b.com' } });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'pw' } });
    expect(btn).not.toBeDisabled();
  });
});
