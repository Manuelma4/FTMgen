import type { CompareRow } from '../types';

interface CompareTableProps {
  rows: CompareRow[];
}

function rowKey(row: CompareRow): string {
  return [
    row.room_id,
    row.niveau,
    row.occupation,
    row.piece,
    row.numero,
    row.categorie,
    row.materiel,
    row.source_room,
    row.source_material,
    row.origin,
    row.pages,
  ].join('|');
}

export function CompareTable({ rows }: CompareTableProps) {
  return (
    <section className="panel compare-panel">
      <h2>Comparatif quantité marché / quantité après FTM</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Niveau</th>
              <th>Occupation</th>
              <th>Pièce Excel</th>
              <th>Source PDF</th>
              <th>Catégorie</th>
              <th>Matériel Excel</th>
              <th>Quantité marché</th>
              <th>Quantité après FTM</th>
              <th>Écart</th>
              <th>Statut</th>
              <th>Pages</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={rowKey(row)}>
                <td>{row.niveau || '-'}</td>
                <td>{row.occupation || '-'}</td>
                <td>
                  {row.piece || '-'}
                  {row.numero ? ` · n° ${row.numero}` : ''}
                </td>
                <td className="wrap">
                  {row.source_room || row.source_material ? (
                    <>
                      {row.source_room || '-'}
                      {row.source_material ? <><br />{row.source_material}</> : null}
                      {row.origin === 'manual' ? <><br /><small>Ajout manuel</small></> : null}
                    </>
                  ) : '-'}
                </td>
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
