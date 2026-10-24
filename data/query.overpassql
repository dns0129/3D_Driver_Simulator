[out:json][timeout:300];
area["name"="黄浦区"]["admin_level"="6"]->.hp;
rel(pivot.hp);
out tags;
(
  way["building"](area.hp);
  relation["building"](area.hp);
  way["building:part"](area.hp);
  relation["building:part"](area.hp);
);
out body geom;
