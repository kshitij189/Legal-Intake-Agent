"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, setDisplayTimeZone, type Staff } from "@/lib/api";

const StaffContext = createContext<Staff | null>(null);

/** The signed-in staff member. Only rendered inside AuthProvider once signed in. */
export function useStaff(): Staff {
  const staff = useContext(StaffContext);
  if (!staff) throw new Error("useStaff outside a signed-in page");
  return staff;
}

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/escalations", label: "Escalation queue" },
  { href: "/intakes", label: "Intakes" },
  { href: "/appointments", label: "Bookings" },
  { href: "/workflows", label: "Workflows" },
];

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [staff, setStaff] = useState<Staff | null>(null);
  const [checked, setChecked] = useState(false);
  const onLogin = pathname === "/login";

  useEffect(() => {
    if (onLogin) {
      setChecked(true);
      return;
    }
    api
      .me()
      .then((me) => {
        setDisplayTimeZone(me.firm_timezone);
        setStaff(me);
      })
      .catch(() => router.replace(`/login?next=${encodeURIComponent(pathname)}`))
      .finally(() => setChecked(true));
  }, [onLogin, pathname, router]);

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setStaff(null);
    router.replace("/login");
  }, [router]);

  if (onLogin) return <>{children}</>;
  if (!checked || !staff) return <p className="muted page">Checking sign-in…</p>;

  return (
    <StaffContext.Provider value={staff}>
      <header className="topbar">
        <div className="brand">
          {staff.firm_name} <span className="muted">· intake review (fictional demo)</span>
        </div>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={pathname === item.href ? "active" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="who">
          <span data-testid="signed-in-as">
            {staff.full_name} ({staff.role.replace("_", " ").toLowerCase()})
          </span>
          <button type="button" className="link" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <main className="page">{children}</main>
    </StaffContext.Provider>
  );
}
