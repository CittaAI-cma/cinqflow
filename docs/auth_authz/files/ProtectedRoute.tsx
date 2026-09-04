import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "./AuthContext";

type Props = {
  children: ReactNode;
  role?: string;
  permission?: string;
};

export function ProtectedRoute({ children, role, permission }: Props) {
  const { user, loading, hasRole, hasPermission } = useAuth();

  if (loading) return null; // swap for a spinner if you have one
  if (!user) return <Navigate to="/login" replace />;
  if (role && !hasRole(role)) return <Navigate to="/403" replace />;
  if (permission && !hasPermission(permission)) return <Navigate to="/403" replace />;

  return <>{children}</>;
}
