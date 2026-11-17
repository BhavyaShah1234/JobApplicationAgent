export default function FetchError({ error, reload }) {
  if (!error) return null;
  return <div className="box warn" role="alert">Updates are unavailable. Displayed data may be old. <button type="button" className="act sec sm" onClick={reload}>Retry updates</button></div>;
}
