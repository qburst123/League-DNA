export function projectHistoricalRow(row){
 return Array.from({length:30},(_,index)=>{const currentDay=index+1,historicalDay=currentDay-row.alignment_offset;
  return {currentDay,historicalDay,card:historicalDay>=1&&historicalDay<=30?row.historical_cards[historicalDay-1]:null,
   captured:currentDay>=row.current_start&&currentDay<=row.current_end};
 });
}
export function trailCondition(row,currentCards,prefix){
 if(prefix<row.current_end)return {state:'waiting',label:'Waiting for current records'};
 for(let day=row.current_start;day<=prefix;day++){
  const historicDay=day-row.alignment_offset;
  if(historicDay<1||historicDay>30)return {state:'boundary',label:'Historical boundary reached',day};
  const actual=currentCards[day-1]?.ft,old=row.historical_cards[historicDay-1]?.ft;
  if(actual==null||old==null)return {state:'waiting',label:'Waiting at a score gap',day};
  if(actual!==old)return {state:'broken',label:`Retained · different at MD${day}`,day,capturedChanged:day<=row.current_end};
 }
 return {state:prefix===30?'completed':'matching',label:prefix===30?'Season completed':'Still aligned'};
}
export function consecutiveClosed(season){
 const days=new Set((season?.days||[]).filter(d=>d.status==='complete').map(d=>d.day));
 let end=0;while(days.has(end+1)&&end<30)end++;return end;
}
