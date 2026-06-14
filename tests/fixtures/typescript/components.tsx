// Fixture: React function components, including hooks and JSX (TSX).
// Expected discovery: `Greeting` (function component), `Counter` (arrow
// component with useState/useEffect hooks), and the nested `handleClick`
// defined inside `Counter`. `useEffect`'s callback is an inline callback (same
// open question as arrows.ts).

import { useEffect, useState } from "react";

interface GreetingProps {
  name: string;
}

export function Greeting({ name }: GreetingProps) {
  return <h1>Hello, {name}</h1>;
}

export const Counter = ({ initial }: { initial: number }) => {
  const [count, setCount] = useState(initial);

  useEffect(() => {
    document.title = `count: ${count}`;
  }, [count]);

  const handleClick = () => {
    setCount((c) => c + 1);
  };

  return (
    <button onClick={handleClick} type="button">
      {count}
    </button>
  );
};
