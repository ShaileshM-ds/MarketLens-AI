// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));

vi.mock('./api', () => ({
  api: apiMock,
  setCsrf: vi.fn(),
  money: (value: number | null | undefined) => String(value ?? 'Not observed'),
  exportJSON: vi.fn(),
}));

vi.mock('./Chart', () => ({ default: () => null }));

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe('application configuration gate', () => {
  afterEach(cleanup);

  beforeEach(() => {
    apiMock.mockReset();
  });

  it('fails closed without data queries or mode and account controls', async () => {
    apiMock.mockRejectedValue(new Error('Configuration request failed'));

    renderApp();

    expect((await screen.findByRole('alert')).textContent).toContain(
      'Application configuration is unavailable',
    );
    expect(apiMock.mock.calls.map(([path]) => path)).toEqual(['/config']);
    expect(screen.queryByLabelText('Data mode')).toBeNull();
    expect(screen.queryByText('Guest workspace')).toBeNull();
    expect(screen.queryByText('Create a consumer account')).toBeNull();
    expect(screen.queryByText('Start a private demo session')).toBeNull();
  });

  it('initializes review mode with imported data and permits reviewer login only', async () => {
    apiMock.mockImplementation((path: string, mode: string) => {
      if (path === '/config') {
        return Promise.resolve({
          review_mode: true,
          active_modes: ['imported'],
        });
      }
      if (path === '/auth/me') {
        return Promise.reject(new Error('Not signed in'));
      }
      if (path === '/overview') {
        return Promise.resolve({
          products: 0,
          observations: 0,
          candidates: [],
          coverage: [],
          runs: [],
        });
      }
      if (path === '/auth/login' && mode === 'imported') {
        return Promise.resolve({
          user: { id: 'reviewer', username: 'reviewer', role: 'reviewer' },
          csrf: 'csrf-token',
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${path} ${mode}`));
    });

    renderApp();

    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith('/overview', 'imported');
      expect(apiMock).toHaveBeenCalledWith('/auth/me', 'imported');
    });
    expect(
      apiMock.mock.calls.some(
        ([path, mode]) => path !== '/config' && mode === 'demo',
      ),
    ).toBe(false);
    expect(screen.queryByLabelText('Data mode')).toBeNull();

    fireEvent.click(screen.getByText('Guest workspace'));
    expect(await screen.findByText('Sign in with your reviewer account to access imported data.')).toBeTruthy();
    expect(screen.queryByText('Create a consumer account')).toBeNull();
    expect(screen.queryByText('Start a private demo session')).toBeNull();

    fireEvent.change(screen.getByLabelText('Username'), {
      target: { value: 'reviewer' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'reviewer-password-123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith(
        '/auth/login',
        'imported',
        'POST',
        {
          username: 'reviewer',
          password: 'reviewer-password-123',
        },
      );
    });
  });

  it('preserves normal mode choices after successful configuration', async () => {
    apiMock.mockImplementation((path: string, mode: string) => {
      if (path === '/config') {
        return Promise.resolve({
          review_mode: false,
          active_modes: ['demo', 'imported', 'live'],
        });
      }
      if (path === '/auth/me') {
        return Promise.reject(new Error('Not signed in'));
      }
      if (path === '/overview') {
        return Promise.resolve({
          products: 0,
          observations: 0,
          candidates: [],
          coverage: [],
          runs: [],
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${path} ${mode}`));
    });

    renderApp();

    expect(await screen.findByLabelText('Data mode')).toBeTruthy();
    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith('/overview', 'demo');
    });
    expect(screen.getByRole('option', { name: 'Imported data' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Live observations' })).toBeTruthy();
  });

  it('shows imported seller totals and separates source from synthetic history', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/config') {
        return Promise.resolve({
          review_mode: false,
          active_modes: ['imported'],
        });
      }
      if (path === '/auth/me') {
        return Promise.reject(new Error('Not signed in'));
      }
      if (path === '/overview') {
        return Promise.resolve({
          products: 2500,
          sellers: 3015,
          observations: 907500,
          source_observations: 7500,
          synthetic_source_observations: 7500,
          generated_history_observations: 900000,
          synthetic_observations: 907500,
          candidates: [],
          candidate_count: 0,
          coverage: [],
          runs: [],
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });

    renderApp();

    expect(await screen.findByText('Total sellers')).toBeTruthy();
    await waitFor(() => {
      expect(
        screen.getByText('Total sellers').parentElement
          ?.querySelector('strong')?.textContent?.replace(/\D/g, ''),
      ).toBe('3015');
      expect(
        screen.getByText('Price observations').parentElement
          ?.querySelector('strong')?.textContent?.replace(/\D/g, ''),
      ).toBe('907500');
    });
    const banner = screen.getByText(/Imported dataset/).textContent || '';
    expect(banner).toContain('source captures');
    expect(banner).toContain('generated-history observations (synthetic)');
    expect(banner.replace(/\D/g, '')).toContain('7500');
    expect(banner.replace(/\D/g, '')).toContain('900000');
  });
});
