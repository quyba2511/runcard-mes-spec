import { createContext, useContext } from "react";
// Shared app context, in its own module so feature pages (mes.tsx) can use it without importing main.tsx.
export const Context = createContext<any>(null);
export const useApp = () => useContext(Context);
