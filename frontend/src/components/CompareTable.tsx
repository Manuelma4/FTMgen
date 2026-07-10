import type { CompareRow } from '../types';

interface CompareTableProps {
  rows: CompareRow[];
}

export function CompareTable({ rows }: CompareTableProps) {
  return (
    <section className="panel compare-panel">
      <h2>Comparatif avant / après</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Pièce</th>
              <th>Catégorie</th>
              <th>Matériel</th>
              <th>Avant</th>
              <th>Après</th>
              <th>Écart</th>
              <th>Statut</th>
              <th>Pages</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.piece}-${row.categorie}-${row.materiel}`}>
                <td>{row.piece}</td>
                <td>{row.categorie}</td>
                <td className="wrap">{row.materiel}</td>
                <td>{row.quantite_avant}</td>
                <td>{row.quantite_apres}</td>
                <td>{row.ecart > 0 ? `+${row.ecart}` : row.ecart}</td>
                <td>{row.statut}</td>
                <td>{row.pages || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
